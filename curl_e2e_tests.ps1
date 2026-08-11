<#
curl_e2e_tests.ps1

Curl-based mirror of tests/e2e/test_complete_flow.py, for exercising a
LIVE server (host uvicorn or the Docker container) the same way
tests/integration/test_deployment_smoke.py does -- real HTTP, no
TestClient in-process shortcut.

Uses curl.exe (built into Windows 10+ alongside PowerShell's own `curl`
alias -- this script calls curl.exe explicitly so it behaves the same
whether run from PowerShell or a shell where `curl` is aliased to
Invoke-WebRequest) plus ConvertFrom-Json for response-shape assertions,
since cmd.exe has no native JSON parsing.

USAGE
-----
  # Against host (default):
  .\curl_e2e_tests.ps1

  # Against Docker container mapped to a different port:
  .\curl_e2e_tests.ps1 -BaseUrl "http://localhost:8000"

  # API key: reads $env:TINYAGENTOS_TEST_API_KEY first (same convention
  # as test_complete_flow.py / test_deployment_smoke.py), falls back to
  # "sk-test" -- which will 401 unless that literal string happens to be
  # an issued, unrevoked key's hash match. See API_KEY_STORAGE_RUNBOOK.md
  # for issuing a real key and loading it into the env var (requires a
  # full terminal close/reopen on Windows for conda env vars to take).

CONTRACT
--------
Mirrors APIContract in test_complete_flow.py: /api/v1 prefix, singular
"result" key, status "success" (not "completed"), bad task_type -> 500
(flagged as non-ideal in both test files, not fixed here).
#>

param(
    [string]$BaseUrl = "http://localhost:8000"
)

$ApiKey = if ($env:TINYAGENTOS_TEST_API_KEY) { $env:TINYAGENTOS_TEST_API_KEY } else { "sk-test" }

$Passed = 0
$Failed = 0

function Test-Case {
    param(
        [string]$Name,
        [scriptblock]$Body
    )
    Write-Host -NoNewline "  $Name ... "
    try {
        & $Body
        Write-Host "PASS" -ForegroundColor Green
        $script:Passed++
    } catch {
        Write-Host "FAIL" -ForegroundColor Red
        Write-Host "    $($_.Exception.Message)" -ForegroundColor Yellow
        $script:Failed++
    }
}

function Invoke-Api {
    param(
        [string]$Method = "GET",
        [string]$Path,
        [string]$Body,
        [switch]$Auth
    )
    $headers = @()
    if ($Auth) { $headers += @("-H", "X-API-Key: $ApiKey") }
    $args = @("-s", "-o", "resp.json", "-w", "%{http_code}", "-X", $Method, "$BaseUrl$Path") + $headers

    # IMPORTANT: never pass JSON as an inline -d string argument on Windows.
    # PowerShell's native-command argument flattening doesn't reliably escape
    # embedded double quotes for curl.exe's CRT-style argv parsing -- the
    # JSON arrives corrupted, Pydantic 422s on the malformed body, and it
    # masquerades as an auth or validation failure. Writing the body to a
    # temp file and using curl's `-d @file` form sidesteps this entirely:
    # no quote characters ever cross the process boundary as literal text.
    $bodyFile = $null
    if ($Body) {
        $bodyFile = [System.IO.Path]::GetTempFileName()
        [System.IO.File]::WriteAllText($bodyFile, $Body, [System.Text.Encoding]::UTF8)
        $args += @("-H", "Content-Type: application/json", "-d", "@$bodyFile")
    }

    $code = & curl.exe @args
    $json = $null
    if (Test-Path resp.json) {
        $raw = Get-Content resp.json -Raw
        if ($raw) { try { $json = $raw | ConvertFrom-Json } catch {} }
        Remove-Item resp.json -ErrorAction SilentlyContinue
    }
    if ($bodyFile) { Remove-Item $bodyFile -ErrorAction SilentlyContinue }

    return @{ Code = [int]$code; Json = $json }
}

function Assert-Equal($actual, $expected, $label) {
    if ($actual -ne $expected) { throw "$label expected $expected, got $actual" }
}

function Assert-In($actual, $allowed, $label) {
    if ($allowed -notcontains $actual) { throw "$label expected one of ($($allowed -join ',')), got $actual" }
}

Write-Host "`nTarget: $BaseUrl   API key: $ApiKey`n"

# ---------------------------------------------------------------------
Write-Host "-- Health --"

Test-Case "GET /api/v1/health returns 200 + status healthy" {
    $r = Invoke-Api -Path "/api/v1/health"
    Assert-Equal $r.Code 200 "status code"
    Assert-Equal $r.Json.status "healthy" "body.status"
    if (-not $r.Json.timestamp) { throw "missing timestamp field" }
}

Test-Case "GET /health (bare) 404s" {
    $r = Invoke-Api -Path "/health"
    Assert-Equal $r.Code 404 "status code"
}

# ---------------------------------------------------------------------
Write-Host "`n-- Auth enforcement --"

Test-Case "POST /api/v1/tasks without API key -> 401" {
    $r = Invoke-Api -Method POST -Path "/api/v1/tasks" `
        -Body '{"text":"Sample text","task_type":"full_pipeline"}'
    Assert-Equal $r.Code 401 "status code"
}

Test-Case "POST /api/v1/tasks with malformed API key -> 401" {
    $bodyFile = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($bodyFile, '{"text":"Sample text","task_type":"full_pipeline"}', [System.Text.Encoding]::UTF8)
    $args = @("-s", "-o", "resp.json", "-w", "%{http_code}", "-X", "POST", "$BaseUrl/api/v1/tasks",
              "-H", "Content-Type: application/json", "-d", "@$bodyFile",
              "-H", "X-API-Key: not-the-right-prefix")
    $code = & curl.exe @args
    Remove-Item resp.json -ErrorAction SilentlyContinue
    Remove-Item $bodyFile -ErrorAction SilentlyContinue
    Assert-Equal ([int]$code) 401 "status code"
}

# ---------------------------------------------------------------------
Write-Host "`n-- Full create -> execute -> poll workflow --"

$script:TaskId = $null

Test-Case "create + execute full_pipeline task -> 200, status success, result present" {
    $body = '{"text":"TinyAgentOS is a small-footprint agent runtime designed for CPU-bound local inference.","task_type":"full_pipeline","priority":1}'
    $create = Invoke-Api -Method POST -Path "/api/v1/tasks" -Body $body -Auth
    Assert-Equal $create.Code 200 "create status code"
    if (-not $create.Json.task_id) { throw "no task_id in create response (status $($create.Code))" }
    $script:TaskId = $create.Json.task_id

    $exec = Invoke-Api -Method POST -Path "/api/v1/tasks/$($script:TaskId)/execute" -Auth
    Assert-Equal $exec.Code 200 "execute status code"
    Assert-Equal $exec.Json.status "success" "body.status"
    if (-not $exec.Json.result) { throw "no result key in execute response" }
}

Test-Case "full_pipeline result has summary + extraction + evaluation" {
    $body = '{"text":"A short piece of sample text for extraction and review.","task_type":"full_pipeline","priority":1}'
    $create = Invoke-Api -Method POST -Path "/api/v1/tasks" -Body $body -Auth
    if (-not $create.Json.task_id) { throw "task creation failed (status $($create.Code)) -- no task_id returned" }
    $taskId = $create.Json.task_id
    $exec = Invoke-Api -Method POST -Path "/api/v1/tasks/$taskId/execute" -Auth
    $result = $exec.Json.result

    if (-not $result.summary) { throw "missing result.summary" }
    if (-not $result.extraction) { throw "missing result.extraction" }
    foreach ($key in @("key_points","entities","sentiment","topics")) {
        if (-not ($result.extraction.PSObject.Properties.Name -contains $key)) {
            throw "missing result.extraction.$key"
        }
    }
    if (-not $result.evaluation) { throw "missing result.evaluation" }
    if (-not ($result.evaluation.PSObject.Properties.Name -contains "score")) {
        throw "missing result.evaluation.score"
    }
}

Test-Case "re-executing a completed task is idempotent" {
    $body = '{"text":"Idempotency check text.","task_type":"summarize","priority":1}'
    $create = Invoke-Api -Method POST -Path "/api/v1/tasks" -Body $body -Auth
    if (-not $create.Json.task_id) { throw "task creation failed (status $($create.Code)) -- no task_id returned" }
    $taskId = $create.Json.task_id

    $first = Invoke-Api -Method POST -Path "/api/v1/tasks/$taskId/execute" -Auth
    $second = Invoke-Api -Method POST -Path "/api/v1/tasks/$taskId/execute" -Auth

    $firstStr = $first.Json | ConvertTo-Json -Compress -Depth 10
    $secondStr = $second.Json | ConvertTo-Json -Compress -Depth 10
    if ($firstStr -ne $secondStr) { throw "first and second execute responses differ" }
}

# ---------------------------------------------------------------------
Write-Host "`n-- Input validation --"

Test-Case "empty text -> 400/422/500 (see test_complete_flow.py docstring caveat)" {
    $r = Invoke-Api -Method POST -Path "/api/v1/tasks" -Auth `
        -Body '{"text":"   ","task_type":"full_pipeline","priority":1}'
    Assert-In $r.Code @(400,422,500) "status code"
}

Test-Case "unsupported task_type -> 500 (flagged, not fixed -- see docstring)" {
    $r = Invoke-Api -Method POST -Path "/api/v1/tasks" -Auth `
        -Body '{"text":"Some text","task_type":"not_a_real_task_type","priority":1}'
    Assert-Equal $r.Code 500 "status code"
}

Test-Case "unknown task_id -> 404" {
    $r = Invoke-Api -Path "/api/v1/tasks/00000000-0000-0000-0000-000000000000" -Auth
    Assert-Equal $r.Code 404 "status code"
}

# ---------------------------------------------------------------------
Write-Host "`n$Passed passed, $Failed failed`n"
if ($Failed -gt 0) { exit 1 }