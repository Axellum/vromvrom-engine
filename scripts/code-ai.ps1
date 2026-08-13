# Alias PowerShell — coding front tab5-engine (#T204)
# Usage : . .\scripts\code-ai.ps1
#         code-ai "écris une fonction de tri en Python"
#         code-ai "refactore router.py" -File core\router.py

function code-ai {
    param(
        [Parameter(Mandatory = $true, Position = 0)]
        [string]$Prompt,
        [string[]]$File = @(),
        [ValidateSet("moyen", "fort")]
        [string]$Tier,
        [switch]$Json
    )
    $moteurRoot = Split-Path -Parent $PSScriptRoot
    Push-Location $moteurRoot
    try {
        $args = @("-m", "tools.code_front", $Prompt)
        foreach ($f in $File) { $args += @("--file", $f) }
        if ($Tier) { $args += @("--tier", $Tier) }
        if ($Json) { $args += "--json" }
        & python @args
    } finally {
        Pop-Location
    }
}

Write-Host "Alias code-ai chargé (moteur_agents)." -ForegroundColor DarkGray
