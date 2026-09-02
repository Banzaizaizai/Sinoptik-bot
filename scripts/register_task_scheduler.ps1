# Регистрирует три задания в Windows Task Scheduler: 08:00, 13:00 и 20:00 (Europe/Kyiv),
# запускающие run_bot.py через venv-интерпретатор.
#
# Запускать из обычного PowerShell (права администратора не обязательны —
# задания создаются в контексте текущего пользователя).
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\register_task_scheduler.ps1

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$PythonExe  = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$ScriptPath = Join-Path $ProjectDir "run_bot.py"

if (-not (Test-Path $PythonExe)) {
    throw "Не найден venv-интерпретатор: $PythonExe. Сначала создайте venv и установите зависимости (см. README.md)."
}
if (-not (Test-Path $ScriptPath)) {
    throw "Не найден run_bot.py: $ScriptPath"
}

# Утро и день -> прогноз на сегодня, вечер -> на завтра.
$actionMorning = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ScriptPath`" --mode today" -WorkingDirectory $ProjectDir
$actionMidday  = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ScriptPath`" --mode today" -WorkingDirectory $ProjectDir
$actionEvening = New-ScheduledTaskAction -Execute $PythonExe -Argument "`"$ScriptPath`" --mode tomorrow" -WorkingDirectory $ProjectDir

$trigger1 = New-ScheduledTaskTrigger -Daily -At 08:00
$trigger2 = New-ScheduledTaskTrigger -Daily -At 13:00
$trigger3 = New-ScheduledTaskTrigger -Daily -At 20:00

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName "WeatherBotDrohobych-Morning" -Action $actionMorning -Trigger $trigger1 -Settings $settings -Description "Прогноз погоды на сегодня в Telegram (утро)" -Force
Register-ScheduledTask -TaskName "WeatherBotDrohobych-Midday" -Action $actionMidday -Trigger $trigger2 -Settings $settings -Description "Прогноз погоды на сегодня в Telegram (день)" -Force
Register-ScheduledTask -TaskName "WeatherBotDrohobych-Evening" -Action $actionEvening -Trigger $trigger3 -Settings $settings -Description "Прогноз погоды на завтра в Telegram (вечер)" -Force

Write-Output "Готово. Задачи 'WeatherBotDrohobych-Morning/-Midday/-Evening' зарегистрированы на 08:00, 13:00 и 20:00."
Write-Output "Проверить: Get-ScheduledTask -TaskName 'WeatherBotDrohobych-*'"
Write-Output "Запустить вручную сейчас: Start-ScheduledTask -TaskName 'WeatherBotDrohobych-Morning'"
Write-Output "Удалить: Unregister-ScheduledTask -TaskName 'WeatherBotDrohobych-Morning','WeatherBotDrohobych-Midday','WeatherBotDrohobych-Evening' -Confirm:`$false"
