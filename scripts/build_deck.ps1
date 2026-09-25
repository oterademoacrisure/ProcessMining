# POINT: builds a PROFESSIONALLY THEMED DEMO_DECK.pptx by automating installed
# PowerPoint via COM. No downloads. Re-run after editing $slides or the theme.
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_deck.ps1
#   powershell ... build_deck.ps1 -ThemeName "Integral"   # swap theme
#
# Good professional themes available: "Ion Boardroom", "Ion", "Integral",
# "Facet", "Gallery", "Slice", "Retrospect", "Office Theme".
param(
  [string]$OutPath   = "$PSScriptRoot\..\docs\DEMO_DECK.pptx",
  [string]$ThemeName = "Ion Boardroom"
)

$ErrorActionPreference = "Stop"
$OutPath = [System.IO.Path]::GetFullPath($OutPath)

$themeDir = "C:\Program Files\Microsoft Office\root\Document Themes 16"
$thmx = Join-Path $themeDir "$ThemeName.thmx"
if (-not (Test-Path $thmx)) { Write-Output "Theme not found: $thmx (using default)"; $thmx = $null }

# Layout: 1 = Title slide, 2 = Title + bulleted content.
$slides = @(
  @{ Layout=1; Title="Process Mining"; Body="Multi-Source Root-Cause Analysis  |  From scattered logs to a diagnosed incident - with the proven fix.";
     Notes="When production breaks, the answer is usually already in the logs - just scattered across systems no one watches. This platform assembles it automatically." },
  @{ Layout=2; Title="The Problem"; Body=@(
     "Clues scattered across Appian, Mule, infrastructure - no single tool sees the whole picture",
     "3-4 teams spend hours manually correlating dashboards",
     "'How we fixed it last time' lives in people's heads",
     "Result: slow MTTR, repeated firefighting, tribal knowledge") -join "`r`n";
     Notes="Relatable example: a credit-check outage where each system shows only a fragment." },
  @{ Layout=2; Title="The Solution"; Body=@(
     "Scattered logs in -> one diagnosed incident with the root cause, the proven fix, and a ready ServiceNow ticket out",
     "Ingests all sources",
     "Correlates related problems into ONE incident",
     "Diagnoses the root cause across layers",
     "Recommends & files the fix; remembers how it was resolved") -join "`r`n";
     Notes="This is the elevator pitch. Pause here." },
  @{ Layout=2; Title="Architecture"; Body="";
     Notes="Walk left to right: sources -> ingest/detect -> correlate/diagnose -> remediate. Two feedback loops: ServiceNow precedent in, approvals from the UI." },
  @{ Layout=2; Title="How It Works - 6 Steps"; Body=@(
     "1. Ingest - all sources into one store",
     "2. Detect - flag problems per source (rule-based)",
     "3. Correlate - group into ONE incident, across systems",
     "4. Diagnose - AI root cause (Mule + infra + past incidents)",
     "5. Act - recommend fix + file ServiceNow ticket",
     "6. Learn - resolved ticket becomes precedent") -join "`r`n";
     Notes="Emphasize 3 (the engine) and 6 (compounding value)." },
  @{ Layout=2; Title="Mule Pinpoints WHERE It Failed"; Body=@(
     "Appian / infra see THAT it is unhealthy; Mule sees WHERE",
     "SocketTimeout -> the downstream service did not respond",
     "flow.error before any call -> Mule itself",
     "Turns 'integration unhealthy' into 'credit-bureau is down - page them, do not retry our side'") -join "`r`n";
     Notes="Diagnosis shifts from probable to definitive." },
  @{ Layout=2; Title="Institutional Memory (Precedent)"; Body=@(
     "Finds similar PAST incidents + their fixes, scored by confidence",
     "e.g. INC0009999 - credit-bureau-prod - 98% - 'paged vendor, restarted gateway'",
     "Threshold: >= 0.60 confidence to surface; stays silent when unsure",
     "Recommends an action based on the proven fix, citing the ticket") -join "`r`n";
     Notes="Gets smarter every time. Confidence + silence-when-unsure = trust." },
  @{ Layout=2; Title="Remediation - Human in the Loop"; Body=@(
     "AI recommends; a human approves in the UI",
     "Approved fixes run a workflow: validate -> execute -> verify -> report",
     "Allow-list gates commands; progress posted to the ServiceNow ticket",
     "Never auto-remediates without approval") -join "`r`n";
     Notes="Stress safety: deterministic gate + human approval + verify." },
  @{ Layout=2; Title="Live Demo - Real Data, End to End"; Body=@(
     "188 events from 6 sources",
     "-> 10 findings -> 3 incidents",
     "-> cross-pillar root cause (Appian + Mule + Prometheus + Fluentd in ONE incident)",
     "-> matching precedent at 98%, recommended fix, auto-ticketed") -join "`r`n";
     Notes="Real numbers from the actual pipeline, not a mockup." },
  @{ Layout=2; Title="The Value"; Body=@(
     "Faster MTTR - minutes, not hours",
     "Right team, right fix - downstream vs our side",
     "Institutional memory - proven fixes reused, confidence-scored",
     "Trustworthy - cites evidence; silent when unsure; human-gated") -join "`r`n";
     Notes="Tie each row to a moment they saw in the demo." },
  @{ Layout=2; Title="Why It Is Trustworthy"; Body=@(
     "Detection & correlation are deterministic - not the AI guessing",
     "The AI only narrates given evidence; cites real ticket numbers",
     "Honest confidence - cautious with one pillar, confident when layers agree",
     "No precedent below 60%; no remediation without approval") -join "`r`n";
     Notes="Pre-empt the 'is the AI hallucinating?' question." },
  @{ Layout=2; Title="Roadmap"; Body=@(
     "Real-time streaming connectors (live Anypoint / Appian)",
     "Embedding-based precedent reranking (semantic match)",
     "Native ServiceNow approval workflow",
     "Desktop-activity pillar (Soroco / ActivTrak)") -join "`r`n";
     Notes="More pillars = higher confidence; this is where it grows." },
  @{ Layout=1; Title="Thank You"; Body="Logs in -> diagnosed incident, proven fix, ready ticket - getting smarter every time.";
     Notes="Close on the one-liner. Offer to show the live UI." }
)

$pp = New-Object -ComObject PowerPoint.Application
$pp.Visible = -1
$pres = $pp.Presentations.Add()

try { $pres.PageSetup.SlideSize = 17 } catch {}   # 17 = Widescreen (960 x 540 pt)
if ($thmx) { $pres.ApplyTemplate($thmx) }          # professional theme

$i = 1
foreach ($s in $slides) {
  $slide = $pres.Slides.Add($i, [int]$s.Layout)
  $slide.Shapes.Title.TextFrame.TextRange.Text = $s.Title
  if ($s.Body) { try { $slide.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text = $s.Body } catch {} }
  if ($s.Notes) { try { $slide.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text = $s.Notes } catch {} }
  # Footer + slide number on content slides (not the title/closing slides)
  if ($s.Layout -ne 1) {
    try {
      $slide.HeadersFooters.Footer.Visible = -1
      $slide.HeadersFooters.Footer.Text = "Process Mining - Multi-Source RCA   |   Confidential - Demo"
      $slide.HeadersFooters.SlideNumber.Visible = -1
    } catch {}
  }
  $i++
}

# Embed the rendered architecture diagram on the Architecture slide (slide 4).
$archPng = [System.IO.Path]::GetFullPath("$PSScriptRoot\..\docs\architecture.png")
if (Test-Path $archPng) {
  try {
    # Auto-fit the 16:9 image inside the slide, below the title, preserving aspect ratio.
    $W = $pres.PageSetup.SlideWidth; $H = $pres.PageSetup.SlideHeight
    $ar = 1920.0 / 1080.0
    $availW = $W * 0.90
    $availH = $H - 130 - 25
    if (($availW / $ar) -le $availH) { $picW = $availW; $picH = $availW / $ar }
    else { $picH = $availH; $picW = $availH * $ar }
    $picLeft = ($W - $picW) / 2; $picTop = 120
    $pres.Slides.Item(4).Shapes.AddPicture($archPng, 0, -1, $picLeft, $picTop, $picW, $picH) | Out-Null
  } catch { Write-Output "note: could not embed architecture.png ($($_.Exception.Message))" }
}

if (Test-Path $OutPath) { Remove-Item $OutPath -Force }
$pres.SaveAs($OutPath, 24)   # 24 = .pptx
$pres.Close()
$pp.Quit()
Write-Output "Created: $OutPath  ($($slides.Count) slides, theme: $ThemeName)"
