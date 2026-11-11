#requires -Version 5.1
$ErrorActionPreference = 'Stop'
$testDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoDirectory = Split-Path -Parent $testDirectory
. (Join-Path $repoDirectory 'connect.ps1')
function Assert-True([bool]$Condition, [string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws([scriptblock]$Action, [string]$Message) {
    $threw = $false
    try { & $Action > $null } catch { $threw = $true }
    if (-not $threw) { throw $Message }
}
$fixture = "ServerAliveInterval 87" + [char]10 + "Host old-host" + [char]10 + "    HostName example.org" + [char]10 + "Host *" + [char]10 + "    Compression yes" + [char]10
$options = @{ Alias='codex-server'; Hostname='203.0.113.10'; User='codexsvc'; SshPort=22; KeyPath='C:\Test User\.ssh\key' }
$once = New-CodexSshConfigText -Existing $fixture @options
$twice = New-CodexSshConfigText -Existing $once @options
Assert-True ($once -ceq $twice) 'Repeated generation must be idempotent.'
Assert-True ($once.EndsWith($fixture)) 'Original config must be preserved byte-for-byte.'
Assert-True ($once.Contains('IdentityFile "C:/Test User/.ssh/key"')) 'Identity paths with spaces must be quoted.'
Assert-Throws { New-CodexSshConfigText -Existing ('Host old codex-server' + [char]10 + ' HostName x') @options } 'Do not overwrite an unmanaged alias.'
Assert-Throws { New-CodexSshConfigText -Existing '# BEGIN codex-server-bootstrap: codex-server' @options } 'Malformed markers must stop.'
Assert-Throws { New-CodexSshConfigText -Existing ($once.Replace('    IdentitiesOnly yes', '    ProxyCommand custom')) @options } 'Custom managed-block edits must not be overwritten.'
Assert-Throws { Get-CodexSshPublicKey '-----BEGIN OPENSSH PRIVATE KEY-----' } 'Never accept private key data.'
Assert-Throws { Get-CodexSshPublicKey ('ssh-ed25519 AAAA' + [char]10 + 'ssh-ed25519 AAAA') } 'Never accept multiple key lines.'
$bytes = New-Object byte[] 51
$bytes[3] = 11
[Array]::Copy([Text.Encoding]::ASCII.GetBytes('ssh-ed25519'), 0, $bytes, 4, 11)
$bytes[18] = 32
$public = 'ssh-ed25519 ' + [Convert]::ToBase64String($bytes)
Assert-True ((Get-CodexSshPublicKey ($public + ' test-comment')) -ceq $public) 'Public key parsing must preserve type/blob and remove comment.'
Assert-Throws { Get-CodexSshPublicKey ($public.Replace('ssh-ed25519 ', 'ssh-rsa ')) } 'Encoded key type must match the declared type.'

$temporary = Join-Path ([IO.Path]::GetTempPath()) ('codex-connect-tests-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($temporary) > $null
try {
    $config = Join-Path $temporary 'config'
    [IO.File]::WriteAllText($config, $fixture, [Text.UTF8Encoding]::new($false))
    $backup = Set-CodexSshConfig -Path $config -Expected $fixture -Content $once
    Assert-True ([IO.File]::ReadAllText($backup) -ceq $fixture) 'Backup must contain the original file.'
    Assert-True ([IO.File]::ReadAllText($config) -ceq $once) 'Atomic update must publish the new config.'
    $noop = Set-CodexSshConfig -Path $config -Expected $once -Content $once
    Assert-True ($null -eq $noop) 'An idempotent update must not create another backup.'
    Assert-Throws { Set-CodexSshConfig -Path $config -Expected $fixture -Content $once } 'Concurrent config edits must be detected.'
    $ssh = (Get-Command ssh.exe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $resolvedOther = @(& $ssh -G -F $config other-host)
    if ($LASTEXITCODE -ne 0) { throw 'OpenSSH could not parse the generated config.' }
    Assert-True ($resolvedOther -contains 'serveraliveinterval 87') 'Global directives before the original first Host must still apply to other hosts.'
    Assert-True ($resolvedOther -contains 'compression yes') 'Original Host * settings must be preserved.'
    $resolvedNew = @(& $ssh -G -F $config codex-server)
    if ($LASTEXITCODE -ne 0) { throw 'OpenSSH could not resolve the new alias.' }
    Assert-True ($resolvedNew -contains 'hostname 203.0.113.10') 'Concrete alias must point to the requested server.'
    Assert-True ($resolvedNew -contains 'user codexsvc') 'Concrete alias must use the service account.'
    Assert-True ($resolvedNew -contains 'serveraliveinterval 30') 'Specific host defaults must precede global defaults.'
    # Exercise the actual -File entry point under Windows PowerShell 5.1.
    # Every network/key/agent executable is a local stub; no server is contacted.
    $stubDirectory = Join-Path $temporary 'stub tools'
    [IO.Directory]::CreateDirectory($stubDirectory) > $null
    $stub = Join-Path $stubDirectory 'ssh.exe'
    $stubSource = @'
using System;
using System.Diagnostics;
using System.IO;
using System.Text;
public class CodexConnectTestStub {
    public static int Main(string[] args) {
        string name = Path.GetFileNameWithoutExtension(Process.GetCurrentProcess().MainModule.FileName);
        File.AppendAllText(Environment.GetEnvironmentVariable("CODEX_CONNECT_TEST_LOG"),
            name + "|" + String.Join("|", args) + "\n", new UTF8Encoding(false));
        if (name == "ssh-add") {
            Console.Error.WriteLine("Error connecting to agent: No such file or directory");
            return 2;
        }
        if (name == "ssh-keygen") {
            Console.WriteLine(Environment.GetEnvironmentVariable("CODEX_CONNECT_TEST_PUBLIC"));
            return 0;
        }
        if (name == "ssh" && args.Length > 0 &&
            args[args.Length - 1].Contains("command -v codex")) {
            Console.WriteLine("/home/codexsvc/.local/bin/codex");
            Console.WriteLine("codex-cli 0.163.0");
        }
        return 0;
    }
}
'@
    Add-Type -TypeDefinition $stubSource -OutputAssembly $stub -OutputType ConsoleApplication
    foreach ($name in @('scp.exe', 'ssh-keygen.exe', 'ssh-add.exe')) {
        [IO.File]::Copy($stub, (Join-Path $stubDirectory $name))
    }
    $profileDirectory = Join-Path $temporary 'test profile'
    $profileSsh = Join-Path $profileDirectory '.ssh'
    [IO.Directory]::CreateDirectory($profileSsh) > $null
    $fixtureIdentity = Join-Path $profileSsh 'test private key'
    [IO.File]::WriteAllText($fixtureIdentity, 'TEST ONLY - not a private key')
    [IO.File]::WriteAllText(($fixtureIdentity + '.pub'), $public)
    [IO.File]::WriteAllText((Join-Path $profileSsh 'config'), $fixture)
    $log = Join-Path $temporary 'native-args.log'
    $originalProfile = $env:USERPROFILE
    $originalPath = $env:PATH
    $originalPublic = $env:CODEX_CONNECT_TEST_PUBLIC
    $originalLog = $env:CODEX_CONNECT_TEST_LOG
    try {
        $env:USERPROFILE = $profileDirectory
        $env:PATH = $stubDirectory + ';' + $originalPath
        $env:CODEX_CONNECT_TEST_PUBLIC = $public
        $env:CODEX_CONNECT_TEST_LOG = $log
        $windowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
        & $windowsPowerShell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoDirectory 'connect.ps1') -Server 203.0.113.10 -AdminUser root -IdentityFile $fixtureIdentity
        if ($LASTEXITCODE -ne 0) { throw 'PowerShell 5.1 main flow failed with no ssh-agent.' }
    } finally {
        $env:USERPROFILE = $originalProfile
        $env:PATH = $originalPath
        $env:CODEX_CONNECT_TEST_PUBLIC = $originalPublic
        $env:CODEX_CONNECT_TEST_LOG = $originalLog
    }
    $calls = [IO.File]::ReadAllText($log)
    Assert-True ($calls.Contains('ssh-add|-l')) 'The missing-agent branch must run.'
    Assert-True ($calls.Contains('|codex-server|true')) 'Missing ssh-agent must not abort direct key verification.'
    Assert-True ($calls.Contains($fixtureIdentity)) 'Native argument handling must retain paths containing spaces.'
    Assert-True ($calls.Contains('--public-key-file')) 'Main flow must call the managed-account authorization helper.'
    Write-Host 'PASS: config preservation/idempotency, key validation, backups, OpenSSH semantics, and real PS5.1 main flow with a missing ssh-agent.'
} finally {
    $resolvedTemporary = [IO.Path]::GetFullPath($temporary)
    $tempPrefix = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\codex-connect-tests-'
    if (-not $resolvedTemporary.StartsWith($tempPrefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Refusing unsafe test cleanup path.' }
    Remove-Item -LiteralPath $resolvedTemporary -Recurse -Force
}
