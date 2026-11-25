# Script de encerramento seguro do servidor Lector
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$port = 8788
$targetPids = [System.Collections.Generic.HashSet[int]]::new()

# 1. Identificar processos ouvindo na porta 8788
try {
    $connections = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($connections) {
        foreach ($conn in $connections) {
            $foundId = $conn.OwningProcess
            if ($foundId) {
                $proc = Get-Process -Id $foundId -ErrorAction SilentlyContinue
                if ($proc -and ($proc.ProcessName -match '^python|^lector-web')) {
                    $targetPids.Add([int]$foundId) | Out-Null
                }
            }
        }
    }
} catch { }

# 2. Identificar processos python com linha de comando do Lector Web
try {
    $webProcs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.Name -match '^python|^lector-web') -and ($_.CommandLine -like '*epub_translate.web*')
    }
    if ($webProcs) {
        foreach ($p in $webProcs) {
            $targetPids.Add([int]$p.ProcessId) | Out-Null
        }
    }
} catch { }

if ($targetPids.Count -gt 0) {
    Write-Host "[Lector] Finalizando servidor Lector..." -ForegroundColor Yellow
    foreach ($targetId in $targetPids) {
        try {
            Write-Host "  -> Encerrando processo PID $targetId..." -ForegroundColor Yellow
            Stop-Process -Id $targetId -Force -ErrorAction SilentlyContinue
        } catch { }
    }
    Start-Sleep -Milliseconds 600
    Write-Host "[Lector] Servidor finalizado com sucesso. PIDs encerrados: $($targetPids -join ', ')" -ForegroundColor Green
} else {
    Write-Host "[Lector] Nenhum servidor Lector em execucao (porta $port livre)." -ForegroundColor Cyan
}

exit 0
