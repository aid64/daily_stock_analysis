﻿$ErrorActionPreference = 'Stop'

Write-Host '=== Daily Stock Analysis Desktop Build ==='

# DSA_DESKTOP_BUILD=true: build-backend.ps1 will skip --add-data static;static
# (desktop loads frontend via Electron loadFile on apps/dsa-desktop/renderer/web/,
# backend _internal does not need static/). try/finally restores the prior value
# so we do not pollute the caller's shell environment.
$previousDesktopFlag = $env:DSA_DESKTOP_BUILD
try {
  $env:DSA_DESKTOP_BUILD = 'true'
  & "${PSScriptRoot}\build-backend.ps1"
} finally {
  if ($null -eq $previousDesktopFlag) {
    Remove-Item Env:DSA_DESKTOP_BUILD -ErrorAction SilentlyContinue
  } else {
    $env:DSA_DESKTOP_BUILD = $previousDesktopFlag
  }
}

& "${PSScriptRoot}\build-desktop.ps1"

Write-Host 'All builds completed.'
