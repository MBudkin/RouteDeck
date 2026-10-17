"""User-requested Windows EXE replacement; preserves application data."""
import base64
import json
import os
import subprocess
import time
from pathlib import Path

from .updates import file_sha256, UpdateError

HELPER = r'''param([Parameter(Mandatory=$true)][string]$Payload)
$ErrorActionPreference = 'Stop'
$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($Payload)) | ConvertFrom-Json
function FileHash([string]$path) {
    $stream = [IO.File]::OpenRead($path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
}
function WriteInPlace([string]$source,[string]$target) {
    # The EXE is overwritten in place, not renamed: Windows keeps taskbar pins and shortcuts
    # on the same file. A renamed old EXE would take the pin with it and the new version
    # would appear as a separate icon. PyInstaller's one-file parent or antivirus may hold
    # the file briefly after the UI exits, so opening is retried.
    for ($attempt=0; ; $attempt++) {
        try { $out = [IO.File]::Open($target,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::None); break }
        catch [IO.IOException] {
            if ($attempt -ge 39) { throw }
            [Threading.Thread]::Sleep(250)
        }
    }
    try {
        $in = [IO.File]::OpenRead($source)
        try { $out.SetLength(0); $in.CopyTo($out); $out.Flush($true) } finally { $in.Dispose() }
    } finally { $out.Dispose() }
}
$backup = $null
$written = $false
try {
    try { $oldProcess = [Diagnostics.Process]::GetProcessById([int]$p.pid) } catch [ArgumentException] { $oldProcess = $null }
    if ($oldProcess -and -not $oldProcess.WaitForExit(60000)) { throw 'Приложение не закрылось. Обновление не установлено.' }
    if ((FileHash $p.source) -ne $p.sha256) { throw 'SHA-256 обновления изменился.' }
    # One copy of the previous version in the data folder, not next to the program.
    $backup = Join-Path $p.backup_dir 'RouteDeck-previous.exe'
    [IO.File]::Copy($p.target,$backup,$true)
    $written = $true
    WriteInPlace $p.source $p.target
    if ((FileHash $p.target) -ne $p.sha256) { throw 'Не удалось проверить установленный файл.' }
    # Leftovers of the previous installer, which renamed the old EXE next to the new one.
    Get-ChildItem -LiteralPath (Split-Path -Parent $p.target) -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like ((Split-Path -Leaf $p.target) + '.backup-*') -or $_.Name -like ((Split-Path -Leaf $p.target) + '.update-*.tmp') } |
        ForEach-Object { try { $_.Delete() } catch {} }
    @{ok=$true;version=$p.version;backup=$backup} | ConvertTo-Json | Set-Content -LiteralPath $p.result -Encoding UTF8
    if ($p.start) { Start-Process -FilePath $p.target -WorkingDirectory (Split-Path -Parent $p.target) }
    exit 0
} catch {
    if ($written -and $backup -and [IO.File]::Exists($backup)) { try { WriteInPlace $backup $p.target } catch {} }
    @{ok=$false;error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $p.result -Encoding UTF8
    if ($p.notify) {
        try { Add-Type -AssemblyName System.Windows.Forms; [Windows.Forms.MessageBox]::Show('Обновление не установлено. ' + $_.Exception.Message + ' Скачанный EXE можно сохранить вручную.','RouteDeck') | Out-Null } catch {}
    }
    exit 1
}
'''


def helper_command(directory, staged, target, sha256, version, pid, *, start=True, notify=True):
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    helper = directory/'install-update.ps1'
    # BOM keeps Russian messages readable in Windows PowerShell 5.1.
    helper.write_text(HELPER, encoding='utf-8-sig')
    payload = {'source':str(Path(staged).resolve()), 'target':str(Path(target).resolve()),
               'sha256':sha256, 'version':version, 'pid':pid, 'start':start, 'notify':notify,
               'backup_dir':str(directory), 'result':str(directory/'install-result.json')}
    encoded = base64.b64encode(json.dumps(payload,ensure_ascii=False).encode('utf-8')).decode('ascii')
    powershell = str(Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe')
    return [powershell,'-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','RemoteSigned','-File',str(helper),'-Payload',encoded]


def clean_environment(environ=None):
    """Environment for the helper and the restarted EXE without PyInstaller runtime state.

    A one-file EXE passes _PYI_* variables to its children. If the new EXE inherits them,
    it treats itself as part of the old process and looks for python3XX.dll in the old,
    already deleted _MEI folder ("Failed to load Python DLL").
    """
    env = {k: v for k, v in (os.environ if environ is None else environ).items()
           if not k.upper().startswith('_PYI_') and k.upper() != '_MEIPASS2'}
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


def start_install(manager, target, pid):
    staged, release = manager.verified_file()
    target = Path(target).resolve()
    if target.suffix.lower() != '.exe' or not target.is_file() or os.name != 'nt':
        raise UpdateError('unsupported', 'Установка доступна только в Windows EXE. Сохраните новый EXE вручную.')
    # Verify twice: before preparing the helper and in the helper after this app exits.
    if file_sha256(staged) != release['sha256']:
        raise UpdateError('checksum', 'SHA-256 скачанного файла изменился.')
    command = helper_command(manager.directory, staged, target, release['sha256'], release['version'], pid)
    flags = getattr(subprocess,'CREATE_NO_WINDOW',0)
    subprocess.Popen(command,creationflags=flags,close_fds=True,env=clean_environment())
    return {'success':True, 'version':release['version']}
