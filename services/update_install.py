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
function MovePreviousExe([string]$source,[string]$destination) {
    # PyInstaller's one-file parent or antivirus may hold the EXE briefly after the UI exits.
    for ($attempt=0; $attempt -lt 40; $attempt++) {
        try { [IO.File]::Move($source,$destination); return }
        catch [IO.IOException] {
            if ($attempt -eq 39) { throw }
            [Threading.Thread]::Sleep(250)
        }
    }
}
$temp = $null
$moved = $false
try {
    try { $oldProcess = [Diagnostics.Process]::GetProcessById([int]$p.pid) } catch [ArgumentException] { $oldProcess = $null }
    if ($oldProcess -and -not $oldProcess.WaitForExit(60000)) { throw 'Приложение не закрылось. Обновление не установлено.' }
    if ((FileHash $p.source) -ne $p.sha256) { throw 'SHA-256 обновления изменился.' }
    $temp = $p.target + '.update-' + [Guid]::NewGuid().ToString('N') + '.tmp'
    [IO.File]::Copy($p.source,$temp,$false)
    if ((FileHash $temp) -ne $p.sha256) { throw 'Не удалось проверить копию обновления.' }
    $backup = $p.target + '.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
    MovePreviousExe $p.target $backup
    $moved = $true
    [IO.File]::Move($temp,$p.target)
    $temp = $null
    if ((FileHash $p.target) -ne $p.sha256) { throw 'Не удалось проверить установленный файл.' }
    @{ok=$true;version=$p.version;backup=$backup} | ConvertTo-Json | Set-Content -LiteralPath $p.result -Encoding UTF8
    if ($p.start) { Start-Process -FilePath $p.target -WorkingDirectory (Split-Path -Parent $p.target) }
    exit 0
} catch {
    if ($moved -and $backup -and [IO.File]::Exists($backup)) { [IO.File]::Copy($backup,$p.target,$true) }
    @{ok=$false;error=$_.Exception.Message} | ConvertTo-Json | Set-Content -LiteralPath $p.result -Encoding UTF8
    if ($p.notify) {
        try { Add-Type -AssemblyName System.Windows.Forms; [Windows.Forms.MessageBox]::Show('Обновление не установлено. ' + $_.Exception.Message + ' Скачанный EXE можно сохранить вручную.','RouteDeck') | Out-Null } catch {}
    }
    exit 1
} finally {
    if ($temp -and [IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }
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
               'result':str(directory/'install-result.json')}
    encoded = base64.b64encode(json.dumps(payload,ensure_ascii=False).encode('utf-8')).decode('ascii')
    powershell = str(Path(os.environ.get('SystemRoot', r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe')
    return [powershell,'-NoProfile','-NonInteractive','-WindowStyle','Hidden','-ExecutionPolicy','RemoteSigned','-File',str(helper),'-Payload',encoded]


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
    subprocess.Popen(command,creationflags=flags,close_fds=True)
    return {'success':True, 'version':release['version']}
