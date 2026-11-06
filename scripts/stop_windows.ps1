# Stop and remove the FinAlly container (Windows PowerShell). The data volume is kept.
# Idempotent: safe to run when nothing is running.
$Container = "finally"

docker container inspect $Container *> $null
if ($LASTEXITCODE -eq 0) {
    docker rm -f $Container | Out-Null
    Write-Host "Stopped and removed container '$Container'. Data volume 'finally-data' was kept."
} else {
    Write-Host "Container '$Container' is not running."
}
