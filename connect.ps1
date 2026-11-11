#requires -Version 5.1
<#
.SYNOPSIS
Authorize a local SSH key and register a concrete SSH host for Codex desktop.
.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\connect.ps1 -Server 203.0.113.10 -AdminUser root
#>
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9.-]*$')]
    [string]$Server,
    [ValidatePattern('^[a-z_][a-z0-9_-]*$')]
    [string]$AdminUser = 'root',
    [ValidatePattern('^[a-z_][a-z0-9_-]*$')]
    [string]$ServiceUser = 'codexsvc',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_-]*$')]
    [string]$HostAlias,
    [ValidateRange(1, 65535)]
    [int]$Port = 22,
    [string]$IdentityFile,
    [string]$AdminIdentityFile
)
$ErrorActionPreference = 'Stop'

function Get-CodexSshPublicKey([string]$Text) {
    $lines = @($Text -split '\r?\n' | Where-Object { $_.Trim().Length -gt 0 })
    if ($lines.Count -ne 1) { throw 'A public key must contain exactly one nonempty line.' }
    $match = [regex]::Match($lines[0].Trim(), '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com)\s+([A-Za-z0-9+/]+={0,3})(?:\s+.*)?$')
    if (-not $match.Success) { throw 'Invalid public-key format. Private-key contents are never uploaded.' }
    try { $blob = [Convert]::FromBase64String($match.Groups[2].Value) } catch { throw 'Invalid public-key base64.' }
    if ($blob.Length -lt 5) { throw 'Invalid SSH key blob.' }
    $nameLength = [uint32]$blob[0] * 16777216 + [uint32]$blob[1] * 65536 + [uint32]$blob[2] * 256 + [uint32]$blob[3]
    if ($nameLength -gt ($blob.Length - 4)) { throw 'Invalid SSH key type field.' }
    $embeddedType = [Text.Encoding]::ASCII.GetString($blob, 4, [int]$nameLength)
    if ($embeddedType -ne $match.Groups[1].Value) { throw 'SSH public-key type does not match its encoded blob.' }
    return $match.Groups[1].Value + ' ' + $match.Groups[2].Value
}

function New-CodexSshConfigText {
    param([string]$Existing, [string]$Alias, [string]$Hostname, [string]$User, [int]$SshPort, [string]$KeyPath)
    if ($Alias -notmatch '^[A-Za-z0-9][A-Za-z0-9_-]*$') { throw 'Invalid SSH alias.' }
    if ($Hostname -notmatch '^[A-Za-z0-9][A-Za-z0-9.-]*$') { throw 'Invalid server name.' }
    if ($User -notmatch '^[a-z_][a-z0-9_-]*$' -or $SshPort -lt 1 -or $SshPort -gt 65535) { throw 'Invalid SSH user/port.' }
    if ($KeyPath.IndexOfAny([char[]]@([char]34, [char]13, [char]10, [char]0)) -ge 0) { throw 'SSH identity path contains unsupported characters.' }
    $begin = '# BEGIN codex-server-bootstrap: ' + $Alias
    $end = '# END codex-server-bootstrap: ' + $Alias
    $beginPattern = '(?m)^' + [regex]::Escape($begin) + '\r?$'
    $endPattern = '(?m)^' + [regex]::Escape($end) + '\r?$'
    $starts = [regex]::Matches($Existing, $beginPattern)
    $ends = [regex]::Matches($Existing, $endPattern)
    if ($starts.Count -ne $ends.Count -or $starts.Count -gt 1) { throw 'Malformed or duplicate managed SSH markers; config was not changed.' }
    $remainder = $Existing
    if ($starts.Count -eq 1) {
        $pattern = '(?ms)^' + [regex]::Escape($begin) + '\r?\n(?<body>.*?)^' + [regex]::Escape($end) + '(?:\r?\n|$)'
        $block = [regex]::Match($Existing, $pattern)
        if (-not $block.Success) { throw 'Managed SSH markers are out of order.' }
        $hostLines = @()
        foreach ($line in ($block.Groups['body'].Value -split '\r?\n')) {
            if ($line -match '^\s*$') { continue }
            if ($line -notmatch '^\s*(Host|HostName|User|Port|IdentityFile|IdentitiesOnly|ServerAliveInterval|StrictHostKeyChecking)\s+(.+)$') {
                throw 'Managed SSH block contains custom directives; preserve them manually before rerunning.'
            }
            if ($Matches[1] -ieq 'Host') { $hostLines += $Matches[2].Trim() }
        }
        $bodyLines = @($block.Groups['body'].Value -split '\r?\n' | Where-Object { $_.Trim().Length -gt 0 })
        if ($hostLines.Count -ne 2 -or $hostLines[0] -ine $Alias -or $hostLines[1] -ne '*' -or $bodyLines[-1].Trim() -ine 'Host *') {
            throw 'Managed SSH block must contain this alias followed by the controlled Host * boundary.'
        }
        $removeLength = $block.Length
        if ($block.Index -eq 0) {
            $after = $Existing.Substring($removeLength)
            if ($after.StartsWith([string][char]10)) { $removeLength++ }
            elseif ($after.StartsWith(([string][char]13 + [char]10))) { $removeLength += 2 }
        }
        $remainder = $Existing.Remove($block.Index, $removeLength)
    }
    foreach ($line in ($remainder -split '\r?\n')) {
        if ($line -match '^\s*Host\s+([^#]+)') {
            foreach ($pattern in ($Matches[1].Trim() -split '\s+')) {
                if ($pattern.Trim([char]34, [char]39) -ieq $Alias) {
                    throw "SSH alias '$Alias' already belongs to an unmanaged Host block. Choose another -HostAlias."
                }
            }
        }
    }
    $unixKey = $KeyPath.Replace('\', '/').Replace('%', '%%')
    $lines = @($begin, "Host $Alias", "    HostName $Hostname", "    User $User", "    Port $SshPort",
        ('    IdentityFile "' + $unixKey + '"'), '    IdentitiesOnly yes',
        '    ServerAliveInterval 30', '    StrictHostKeyChecking ask', 'Host *', $end)
    return ($lines -join [char]10) + [char]10 + [char]10 + $remainder
}

function Set-CodexSshConfig([string]$Path, [string]$Expected, [string]$Content) {
    if (Test-Path -LiteralPath $Path) {
        if ((Get-Item -LiteralPath $Path).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to replace a linked SSH config.' }
        if ([IO.File]::ReadAllText($Path) -cne $Expected) { throw 'SSH config changed during setup; rerun to preserve concurrent edits.' }
    } elseif ($Expected.Length -gt 0) { throw 'SSH config disappeared during setup.' }
    if ($Expected -ceq $Content) { return $null }
    $backup = $null
    if (Test-Path -LiteralPath $Path) {
        $backup = $Path + '.codex-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
        [IO.File]::Copy($Path, $backup, $false)
    }
    $temporary = $Path + '.codex-tmp-' + [guid]::NewGuid().ToString('N')
    try {
        [IO.File]::WriteAllText($temporary, $Content, [Text.UTF8Encoding]::new($false))
        if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, $backup) }
        else { [IO.File]::Move($temporary, $Path) }
    } finally { if (Test-Path -LiteralPath $temporary) { [IO.File]::Delete($temporary) } }
    return $backup
}

function Assert-CodexNativeSuccess([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE). Setup stopped." }
}

# Dot-sourcing loads helpers for local tests without touching SSH.
if ($MyInvocation.InvocationName -eq '.') { return }
if ([string]::IsNullOrWhiteSpace($Server)) { throw 'Specify -Server with the server IP or DNS name.' }
$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
if ([string]::IsNullOrWhiteSpace($HostAlias)) { $HostAlias = 'codex-server' }
$HostAlias = $HostAlias.ToLowerInvariant()
if ([string]::IsNullOrWhiteSpace($env:USERPROFILE)) { throw 'Windows USERPROFILE is unavailable.' }
$sshDirectory = Join-Path $env:USERPROFILE '.ssh'
if ((Test-Path -LiteralPath $sshDirectory) -and ((Get-Item -LiteralPath $sshDirectory).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Refusing to change a linked .ssh directory.' }
[IO.Directory]::CreateDirectory($sshDirectory) > $null
$configPath = Join-Path $sshDirectory 'config'
$existingConfig = if (Test-Path -LiteralPath $configPath) { [IO.File]::ReadAllText($configPath) } else { '' }
$ssh = (Get-Command ssh.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$scp = (Get-Command scp.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$keygen = (Get-Command ssh-keygen.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$explicitIdentity = -not [string]::IsNullOrWhiteSpace($IdentityFile)
if ($ServiceUser -ne 'codexsvc' -and -not $explicitIdentity) { throw 'For an existing non-codexsvc account, pass an already-authorized -IdentityFile.' }
if ($explicitIdentity) {
    $keyPath = (Resolve-Path -LiteralPath $IdentityFile -ErrorAction Stop).Path
} else { $keyPath = Join-Path $sshDirectory ('id_ed25519_' + $HostAlias) }
$newConfig = New-CodexSshConfigText -Existing $existingConfig -Alias $HostAlias -Hostname $Server -User $ServiceUser -SshPort $Port -KeyPath $keyPath
if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
    if ($explicitIdentity) { throw 'The specified private-key file does not exist.' }
    if (Test-Path -LiteralPath ($keyPath + '.pub')) { throw 'An orphan .pub file already exists; no key was overwritten.' }
    Write-Host "Creating an independent ed25519 key for $HostAlias."
    Write-Host 'A passphrase is optional; Codex desktop needs that key unlocked in ssh-agent.'
    & $keygen -t ed25519 -f $keyPath -C ("codex-server-bootstrap:" + $HostAlias)
    Assert-CodexNativeSuccess 'Generating the SSH key'
}
if ((Get-Item -LiteralPath $keyPath).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing a linked private-key file.' }
# Derive from the private key: never upload an arbitrary .pub file on trust.
$derivedLines = @(& $keygen -y -f $keyPath)
Assert-CodexNativeSuccess 'Reading the public part of the SSH key'
$publicKey = Get-CodexSshPublicKey ($derivedLines -join [char]10)
if (Test-Path -LiteralPath ($keyPath + '.pub')) {
    $sidecarKey = Get-CodexSshPublicKey ([IO.File]::ReadAllText($keyPath + '.pub'))
    if ($sidecarKey -cne $publicKey) { throw 'Existing .pub does not match the private key; no key was uploaded.' }
}

if ($ServiceUser -eq 'codexsvc') {
    $helper = Join-Path $scriptDirectory 'server-authorize-key.sh'
    $releasePath = Join-Path $scriptDirectory 'release.json'
    if (-not (Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'Missing server-authorize-key.sh; keep the repository together.' }
    $release = [IO.File]::ReadAllText($releasePath) | ConvertFrom-Json
    $releaseHash = [string]$release.sha256
    if ($releaseHash -notmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid release.json SHA-256.' }
    $adminArgs = @('-p', "$Port", '-o', 'ConnectTimeout=15', '-o', 'StrictHostKeyChecking=ask')
    $copyArgs = @('-P', "$Port", '-o', 'ConnectTimeout=15', '-o', 'StrictHostKeyChecking=ask')
    if ($AdminIdentityFile) {
        $adminKey = (Resolve-Path -LiteralPath $AdminIdentityFile -ErrorAction Stop).Path
        $adminArgs += @('-i', $adminKey, '-o', 'IdentitiesOnly=yes')
        $copyArgs += @('-i', $adminKey, '-o', 'IdentitiesOnly=yes')
    }
    $adminDestination = "$AdminUser@$Server"
    $stage = '/tmp/codex-ssh-authorize-' + [guid]::NewGuid().ToString('N')
    $localPublicFile = Join-Path ([IO.Path]::GetTempPath()) ('codex-public-' + [guid]::NewGuid().ToString('N') + '.pub')
    try {
        [IO.File]::WriteAllText($localPublicFile, $publicKey + [char]10, [Text.UTF8Encoding]::new($false))
        Write-Host "Authorize the public key through $adminDestination. Enter server/sudo passwords only in this terminal."
        $createStage = 'set -eu' + [char]10 + 'umask 077' + [char]10 + "mkdir -- '$stage'"
        & $ssh @adminArgs $adminDestination $createStage
        Assert-CodexNativeSuccess 'Preparing the private upload directory'
        & $scp @copyArgs $helper $localPublicFile ('{0}:{1}/' -f $adminDestination, $stage)
        Assert-CodexNativeSuccess 'Uploading the authorization helper and public key'
        $publicName = [IO.Path]::GetFileName($localPublicFile)
        & $ssh @adminArgs -tt $adminDestination "bash '$stage/server-authorize-key.sh' --service-user 'codexsvc' --public-key-file '$stage/$publicName' --release-sha256 '$releaseHash'"
        Assert-CodexNativeSuccess 'Authorizing the public key'
    } finally { if (Test-Path -LiteralPath $localPublicFile) { [IO.File]::Delete($localPublicFile) } }
}

$backup = Set-CodexSshConfig -Path $configPath -Expected $existingConfig -Content $newConfig
if ($backup) { Write-Host "Previous SSH config backed up to $backup" }
Write-Host "Registered SSH Host $HostAlias in $configPath."
$sshAddCommand = Get-Command ssh-add.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if ($sshAddCommand) {
    $previousErrorPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $sshAddCommand.Source -l *> $null
        $agentStatus = $LASTEXITCODE
    } finally { $ErrorActionPreference = $previousErrorPreference }
    if ($agentStatus -eq 0 -or $agentStatus -eq 1) {
        & $sshAddCommand.Source $keyPath
        if ($LASTEXITCODE -ne 0) { Write-Host 'ssh-agent did not accept the key; checking whether direct key authentication works.' }
    }
}
$verifyArgs = @('-T', '-F', $configPath, '-o', 'ConnectTimeout=15', '-o', 'BatchMode=yes',
    '-o', 'PasswordAuthentication=no', '-o', 'KbdInteractiveAuthentication=no')
$quiet = @(& $ssh @verifyArgs $HostAlias 'true')
if ($LASTEXITCODE -ne 0) {
    throw 'Noninteractive key login failed. The Host block and public-key authorization are retained. If the key has a passphrase, start your existing Windows ssh-agent and run ssh-add with this key, then rerun. SSH stderr above may also identify host-key or account restrictions.'
}
if (($quiet -join '').Trim().Length -ne 0) { throw 'Remote noninteractive shell prints extra stdout. Remove startup echo/output before using Codex app-server.' }
$probe = @(& $ssh @verifyArgs $HostAlias "bash -lc 'command -v codex && codex --version'")
Assert-CodexNativeSuccess 'Checking remote Codex'
$probeLines = @($probe | ForEach-Object { $_.ToString().Trim() } | Where-Object { $_.Length -gt 0 })
if ($probeLines.Count -ne 2 -or $probeLines[0] -notmatch '^/' -or $probeLines[1] -notmatch '^codex(?:-cli)?\s+\S+$') {
    throw 'Remote login-shell output is unexpected. Codex must be on the login PATH and shell startup must not print banners.'
}
Write-Host "Verified: $($probeLines[0]) ; $($probeLines[1])"
Write-Host "Codex desktop SSH host is ready: $HostAlias"
Write-Host "Choose this SSH host in Codex desktop. Manual connection: ssh $HostAlias"
