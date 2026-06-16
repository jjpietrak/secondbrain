#!/bin/bash
# Register the Tier-2 local nightly run in Windows Task Scheduler (run ONCE from WSL).
# More reliable than cron under WSL: Task Scheduler can wake the machine.
set -euo pipefail

WSL_EXE='C:\\Windows\\System32\\wsl.exe'
ARGS='-d Ubuntu -- /home/jpietrak/second_brain/agents/nightly_run.sh'
TASK_NAME='SecondBrainNightly'

powershell.exe -NoProfile -Command "
\$action  = New-ScheduledTaskAction -Execute '$WSL_EXE' -Argument '$ARGS'
\$trigger = New-ScheduledTaskTrigger -Daily -At '2:00AM'
\$settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName '$TASK_NAME' -Action \$action -Trigger \$trigger -Settings \$settings -Force
Write-Output 'Registered task: $TASK_NAME (daily 02:00)'
"
echo "Windows Task Scheduler job registered: $TASK_NAME"
echo "Tip: test it now with -> DRY_RUN=1 bash /home/jpietrak/second_brain/agents/nightly_run.sh"
