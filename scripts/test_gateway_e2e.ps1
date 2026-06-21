<#
.SYNOPSIS
    End-to-end validation of the MCP web gateway running locally.

.DESCRIPTION
    Tests 7 scenarios against a locally-running mcp-web-gateway:
      1. Unauthenticated GET /sse → 401
      2. 401 response contains WWW-Authenticate with resource_metadata hint
      3. Protected-resource metadata is correct
      4. Valid token with read scopes → 200 on /sse (SSE stream opens)
      5. Invalid token → 401
      6. Valid token but missing scope blocks a protected tool call
      7. A valid read-only tool call proxies through (requires internal MCP running)

.PARAMETER GatewayUrl
    Base URL of the local gateway (default: http://localhost:8007)

.PARAMETER JwtSecret
    HS256 secret matching OAUTH_JWT_SECRET in your .env (default: local-dev-secret)

.PARAMETER Audience
    JWT audience matching OAUTH_AUDIENCE (default: mcp-web-gateway)

.PARAMETER SkipProxyTest
    Skip test 7 (requires the internal mcp-server to be running on MCP_INTERNAL_URL)

.EXAMPLE
    # Start the gateway first:
    #   $env:OAUTH_JWT_SECRET="local-dev-secret"; uvicorn services.mcp_web_gateway.main:app --port 8007
    .\scripts\test_gateway_e2e.ps1

.EXAMPLE
    .\scripts\test_gateway_e2e.ps1 -GatewayUrl "https://mcp-web-gateway.up.railway.app" `
        -JwtSecret "your-prod-secret" -SkipProxyTest
#>
param(
    [string]$GatewayUrl   = "http://localhost:8007",
    [string]$JwtSecret    = "local-dev-secret",
    [string]$Audience     = "mcp-web-gateway",
    [switch]$SkipProxyTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ───────────────────────────────────────────────────────────────────

$pass = 0
$fail = 0

function Pass([string]$name) {
    Write-Host "  [PASS] $name" -ForegroundColor Green
    $script:pass++
}

function Fail([string]$name, [string]$detail) {
    Write-Host "  [FAIL] $name" -ForegroundColor Red
    Write-Host "         $detail" -ForegroundColor DarkRed
    $script:fail++
}

function MakeToken {
    param(
        [string]$Sub       = "e2e-user",
        [string]$ClientId  = "e2e-client",
        [string]$Scope     = "mcp:tools:read mcp:account:read mcp:strategy:read",
        [string]$Aud       = $Audience,
        [int]   $TtlSec    = 3600
    )
    # Base64url-encode JSON without padding
    function B64Url([string]$s) {
        $bytes  = [System.Text.Encoding]::UTF8.GetBytes($s)
        $b64    = [Convert]::ToBase64String($bytes)
        return $b64.TrimEnd("=").Replace("+","-").Replace("/","_")
    }

    $now     = [int](Get-Date -UFormat %s)
    $header  = B64Url '{"alg":"HS256","typ":"JWT"}'
    $payload = B64Url (ConvertTo-Json @{
        sub   = $Sub
        azp   = $ClientId
        scope = $Scope
        aud   = $Aud
        iat   = $now
        exp   = $now + $TtlSec
    } -Compress)
    $sigInput = "$header.$payload"
    $keyBytes = [System.Text.Encoding]::UTF8.GetBytes($JwtSecret)
    $msgBytes = [System.Text.Encoding]::UTF8.GetBytes($sigInput)
    $hmac     = New-Object System.Security.Cryptography.HMACSHA256
    $hmac.Key = $keyBytes
    $sigBytes = $hmac.ComputeHash($msgBytes)
    $sig      = [Convert]::ToBase64String($sigBytes).TrimEnd("=").Replace("+","-").Replace("/","_")
    return "$sigInput.$sig"
}

function Invoke-GatewayRequest {
    param(
        [string]$Method  = "GET",
        [string]$Path,
        [hashtable]$Headers = @{},
        [object]$Body    = $null,
        [switch]$NoThrow
    )
    $uri = "$GatewayUrl$Path"
    $params = @{ Method = $Method; Uri = $uri; Headers = $Headers }
    if ($Body) { $params.Body = ($Body | ConvertTo-Json -Compress); $params.ContentType = "application/json" }
    try {
        return Invoke-RestMethod @params -TimeoutSec 10
    } catch {
        if ($NoThrow) { return $_.Exception.Response }
        throw
    }
}

function GetStatusCode([string]$Method, [string]$Path, [hashtable]$Headers = @{}, [object]$Body = $null) {
    $uri = "$GatewayUrl$Path"
    $params = @{ Method = $Method; Uri = $uri; Headers = $Headers; UseBasicParsing = $true }
    if ($Body) { $params.Body = ($Body | ConvertTo-Json -Compress); $params.ContentType = "application/json" }
    try {
        $resp = Invoke-WebRequest @params -TimeoutSec 10 -ErrorAction SilentlyContinue
        return [int]$resp.StatusCode, $resp
    } catch {
        $status = [int]$_.Exception.Response.StatusCode
        return $status, $_.Exception.Response
    }
}

# ── Preflight ─────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "MCP Web Gateway — E2E validation" -ForegroundColor Cyan
Write-Host "  Gateway : $GatewayUrl" -ForegroundColor DarkGray
Write-Host "  Audience: $Audience" -ForegroundColor DarkGray
Write-Host ""

# Check gateway is reachable
try {
    $health = Invoke-GatewayRequest -Path "/health"
    Write-Host "  Gateway reachable — status=$($health.status) oauth_mode=$($health.oauth_mode)" -ForegroundColor DarkGray
} catch {
    Write-Host "ERROR: Gateway not reachable at $GatewayUrl" -ForegroundColor Red
    Write-Host "  Start it first:  uvicorn services.mcp_web_gateway.main:app --port 8007" -ForegroundColor Yellow
    exit 1
}
Write-Host ""

# ── Test 1: Unauthenticated → 401 ─────────────────────────────────────────────

Write-Host "Test 1: Unauthenticated GET /sse → 401"
$status1, $resp1 = GetStatusCode -Method "GET" -Path "/sse"
if ($status1 -eq 401) { Pass "Unauthenticated /sse returns 401" }
else { Fail "Unauthenticated /sse returns 401" "Got $status1" }

# ── Test 2: WWW-Authenticate header ──────────────────────────────────────────

Write-Host "Test 2: 401 includes WWW-Authenticate with resource_metadata hint"
try {
    $wwwAuth = ""
    if ($resp1 -is [System.Net.HttpWebResponse]) {
        $wwwAuth = $resp1.Headers["WWW-Authenticate"]
    }
    # Fallback: re-issue request and capture header
    if (-not $wwwAuth) {
        $req = [System.Net.HttpWebRequest]::Create("$GatewayUrl/sse")
        $req.Method = "GET"
        $req.Timeout = 10000
        try { $req.GetResponse() | Out-Null } catch {
            $wwwAuth = $_.Exception.Response.Headers["WWW-Authenticate"]
        }
    }
    if ($wwwAuth -and $wwwAuth -match "resource_metadata") {
        Pass "WWW-Authenticate contains resource_metadata"
    } else {
        Fail "WWW-Authenticate contains resource_metadata" "Got: '$wwwAuth'"
    }
} catch {
    Fail "WWW-Authenticate header check" $_.Exception.Message
}

# ── Test 3: Protected-resource metadata ──────────────────────────────────────

Write-Host "Test 3: /.well-known/oauth-protected-resource shape"
try {
    $meta = Invoke-GatewayRequest -Path "/.well-known/oauth-protected-resource"
    $ok = $true
    if (-not $meta.resource)                          { $ok = $false; Write-Host "    missing: resource" -ForegroundColor DarkRed }
    if (-not $meta.authorization_servers)             { $ok = $false; Write-Host "    missing: authorization_servers" -ForegroundColor DarkRed }
    if ($meta.bearer_methods_supported -notcontains "header") { $ok = $false; Write-Host "    missing: bearer_methods_supported=header" -ForegroundColor DarkRed }
    if (-not ($meta.scopes_supported -contains "mcp:tools:read"))    { $ok = $false }
    if (-not ($meta.scopes_supported -contains "mcp:account:read"))  { $ok = $false }
    if (-not ($meta.scopes_supported -contains "mcp:strategy:read")) { $ok = $false }
    if ($ok) { Pass "Protected-resource metadata shape is correct" }
    else      { Fail "Protected-resource metadata shape is correct" "See details above" }
} catch {
    Fail "Protected-resource metadata" $_.Exception.Message
}

# ── Test 4: Valid token opens SSE ─────────────────────────────────────────────

Write-Host "Test 4: Valid token → SSE stream opens (reads first event line)"
try {
    $token = MakeToken
    $req = [System.Net.HttpWebRequest]::Create("$GatewayUrl/sse")
    $req.Method = "GET"
    $req.Headers.Add("Authorization", "Bearer $token")
    $req.Accept = "text/event-stream"
    $req.Timeout = 8000
    $resp4 = $req.GetResponse()
    $stream = $resp4.GetResponseStream()
    $reader = New-Object System.IO.StreamReader($stream)
    $line = $reader.ReadLine()
    $reader.Close(); $resp4.Close()
    if ($line -match "event: endpoint" -or $line -match "^event:") {
        Pass "Valid token opens SSE stream (got endpoint event)"
    } else {
        Pass "Valid token opens SSE stream (status 200, first line: '$line')"
    }
} catch {
    Fail "Valid token opens SSE stream" $_.Exception.Message
}

# ── Test 5: Invalid token → 401 ──────────────────────────────────────────────

Write-Host "Test 5: Invalid/tampered token → 401"
$status5, $_ = GetStatusCode -Method "GET" -Path "/sse" -Headers @{ Authorization = "Bearer not.a.real.token" }
if ($status5 -eq 401) { Pass "Invalid token returns 401" }
else { Fail "Invalid token returns 401" "Got $status5" }

# ── Test 6: Scope insufficient → error response via /messages ─────────────────

Write-Host "Test 6: Token with only mcp:tools:read blocked from mcp:account:read tool"
try {
    # Open an SSE session with narrow scope
    $narrowToken = MakeToken -Scope "mcp:tools:read"
    $req6 = [System.Net.HttpWebRequest]::Create("$GatewayUrl/sse")
    $req6.Method = "GET"
    $req6.Headers.Add("Authorization", "Bearer $narrowToken")
    $req6.Accept = "text/event-stream"
    $req6.Timeout = 8000
    $resp6 = $req6.GetResponse()
    $stream6 = $resp6.GetResponseStream()
    $reader6 = New-Object System.IO.StreamReader($stream6)

    # Read the endpoint event to get session_id
    $sessionId = $null
    $deadline6 = (Get-Date).AddSeconds(5)
    while ((Get-Date) -lt $deadline6) {
        $line6 = $reader6.ReadLine()
        if ($line6 -match "session_id=(.+)$") {
            $sessionId = $Matches[1].Trim()
            break
        }
    }

    if ($sessionId) {
        # Post a tools/call that requires mcp:account:read
        $msgBody = @{
            jsonrpc = "2.0"; id = 1; method = "tools/call"
            params  = @{ name = "get_account_balances"; arguments = @{} }
        }
        $postResp = Invoke-RestMethod -Method POST -Uri "$GatewayUrl/messages?session_id=$sessionId" `
            -Body ($msgBody | ConvertTo-Json -Compress) -ContentType "application/json" -TimeoutSec 5 `
            -ErrorAction SilentlyContinue

        # Read the SSE error response
        $errorLine = ""
        $deadline6b = (Get-Date).AddSeconds(5)
        while ((Get-Date) -lt $deadline6b) {
            $line6 = $reader6.ReadLine()
            if ($line6 -match '"error"') { $errorLine = $line6; break }
        }
        $reader6.Close(); $resp6.Close()

        if ($errorLine -match '"error"') {
            Pass "Missing scope blocks get_account_balances (error in SSE response)"
        } else {
            Fail "Missing scope blocks get_account_balances" "No error in SSE stream"
        }
    } else {
        $reader6.Close(); $resp6.Close()
        Fail "Missing scope test" "Could not parse session_id from SSE endpoint event"
    }
} catch {
    Fail "Missing scope test" $_.Exception.Message
}

# ── Test 7: Proxy round-trip (optional) ──────────────────────────────────────

if (-not $SkipProxyTest) {
    Write-Host "Test 7: Valid full-scope token → get_stream_health proxies to internal MCP"
    try {
        $fullToken = MakeToken
        $req7 = [System.Net.HttpWebRequest]::Create("$GatewayUrl/sse")
        $req7.Method = "GET"
        $req7.Headers.Add("Authorization", "Bearer $fullToken")
        $req7.Accept = "text/event-stream"
        $req7.Timeout = 30000
        $resp7 = $req7.GetResponse()
        $stream7 = $resp7.GetResponseStream()
        $reader7 = New-Object System.IO.StreamReader($stream7)

        # Read session_id
        $sessionId7 = $null
        $deadline7 = (Get-Date).AddSeconds(5)
        while ((Get-Date) -lt $deadline7) {
            $line7 = $reader7.ReadLine()
            if ($line7 -match "session_id=(.+)$") { $sessionId7 = $Matches[1].Trim(); break }
        }

        if ($sessionId7) {
            # initialize
            $initBody = @{ jsonrpc="2.0"; id=1; method="initialize"; params=@{ protocolVersion="2024-11-05"; capabilities=@{}; clientInfo=@{name="e2e-test";version="1.0"} } }
            Invoke-RestMethod -Method POST -Uri "$GatewayUrl/messages?session_id=$sessionId7" `
                -Body ($initBody | ConvertTo-Json -Depth 5 -Compress) -ContentType "application/json" -TimeoutSec 5 | Out-Null

            # Read initialize response
            $initResp = ""
            $deadline7b = (Get-Date).AddSeconds(5)
            while ((Get-Date) -lt $deadline7b) {
                $line7 = $reader7.ReadLine()
                if ($line7 -match '"result"') { $initResp = $line7; break }
            }

            # notifications/initialized
            $notifBody = @{ jsonrpc="2.0"; method="notifications/initialized"; params=@{} }
            Invoke-RestMethod -Method POST -Uri "$GatewayUrl/messages?session_id=$sessionId7" `
                -Body ($notifBody | ConvertTo-Json -Compress) -ContentType "application/json" -TimeoutSec 5 | Out-Null

            # tools/call get_stream_health
            $callBody = @{ jsonrpc="2.0"; id=2; method="tools/call"; params=@{ name="get_stream_health"; arguments=@{} } }
            Invoke-RestMethod -Method POST -Uri "$GatewayUrl/messages?session_id=$sessionId7" `
                -Body ($callBody | ConvertTo-Json -Compress) -ContentType "application/json" -TimeoutSec 5 | Out-Null

            # Read tool result from SSE
            $toolResult = ""
            $deadline7c = (Get-Date).AddSeconds(15)
            while ((Get-Date) -lt $deadline7c) {
                $line7 = $reader7.ReadLine()
                if ($line7 -match '"result"' -and $line7 -match '"id":2') { $toolResult = $line7; break }
                if ($line7 -match '"error"')                               { $toolResult = $line7; break }
            }
            $reader7.Close(); $resp7.Close()

            if ($toolResult -match '"result"') {
                Pass "get_stream_health proxied successfully (got result)"
            } elseif ($toolResult -match '"error"') {
                Fail "get_stream_health proxy" "Got error: $toolResult"
            } else {
                Fail "get_stream_health proxy" "No response received within timeout"
            }
        } else {
            $reader7.Close(); $resp7.Close()
            Fail "Proxy test" "Could not parse session_id"
        }
    } catch {
        Fail "Proxy test" $_.Exception.Message
    }
} else {
    Write-Host "  [SKIP] Test 7: proxy round-trip (SkipProxyTest)" -ForegroundColor DarkYellow
}

# ── Summary ───────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "─────────────────────────────────────────" -ForegroundColor DarkGray
$total = $pass + $fail
if ($fail -eq 0) {
    Write-Host "  $pass/$total tests passed" -ForegroundColor Green
} else {
    Write-Host "  $pass/$total passed, $fail FAILED" -ForegroundColor Red
}
Write-Host "─────────────────────────────────────────" -ForegroundColor DarkGray
Write-Host ""

if ($fail -gt 0) { exit 1 }
