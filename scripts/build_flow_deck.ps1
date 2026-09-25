# POINT: builds DEMO_FLOW.pptx (the ARCHITECT'S PRESENTER RUNBOOK) from the
# DEMO_FLOW guide, via installed PowerPoint COM. No downloads.
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_flow_deck.ps1
#   powershell ... build_flow_deck.ps1 -ThemeName "Integral"
param(
  [string]$OutPath   = "$PSScriptRoot\..\docs\DEMO_FLOW.pptx",
  [string]$ThemeName = "Ion Boardroom"
)

$ErrorActionPreference = "Stop"
$OutPath = [System.IO.Path]::GetFullPath($OutPath)
$themeDir = "C:\Program Files\Microsoft Office\root\Document Themes 16"
$thmx = Join-Path $themeDir "$ThemeName.thmx"
if (-not (Test-Path $thmx)) { Write-Output "Theme not found: $thmx (using default)"; $thmx = $null }

$slides = @(
  @{ Layout=1; Title="Demo Flow - Architect's Guide"; Body="How to present the Process Mining Multi-Source RCA platform";
     Notes="Your presenter runbook - what to say, what to click, in order." },
  @{ Layout=2; Title="The 30-Second Pitch"; Body=@(
     "When production breaks, clues are scattered across many systems - no single tool sees the whole picture",
     "The platform gathers them, correlates into ONE incident, diagnoses the root cause, recommends & files the fix",
     "And it remembers how we fixed it last time",
     "Hours of cross-team firefighting become one diagnosed incident") -join "`r`n";
     Notes="Say this verbatim or in your own words. Pause after." },
  @{ Layout=2; Title="What It Does - 6 Steps"; Body=@(
     "1. Ingest - all sources into one store",
     "2. Detect - flag problems per source",
     "3. Correlate - group related problems into ONE incident",
     "4. Diagnose - AI root cause (Mule + infra + precedent)",
     "5. Act - recommend fix + file ServiceNow ticket",
     "6. Learn - resolved ticket becomes precedent") -join "`r`n";
     Notes="Steps 3 and 6 are the heart: correlation + learning." },
  @{ Layout=2; Title="Three Pillars, One Incident"; Body=@(
     "BPM / integration: Appian (x3), Mule",
     "Infrastructure / runtime: Prometheus, Fluentd",
     "Desktop: not wired yet (roadmap)",
     "Value = correlating ACROSS pillars: downstream vs our-host, backed by a past fix") -join "`r`n";
     Notes="The platform's edge is cross-pillar correlation." },
  @{ Layout=2; Title="Before the Meeting (prep, off-screen)"; Body=@(
     "seed_demo_history.py  ->  load demo precedents",
     "demo_full.py          ->  run all sources, one pass",
     "streamlit run app/ui/streamlit_app.py  ->  leave UI open",
     "Present the pre-run results - no live run needed in front of the customer") -join "`r`n";
     Notes="Run 5 min before. Confirm the top report shows 5 sources + a precedent panel." },
  @{ Layout=2; Title="Live Demo (1) - Frame & Correlate"; Body=@(
     "SAY: 'Credit-check timing out, processes piling up, users erroring, host under memory pressure - all at once. Four teams, four dashboards. Watch this.'",
     "SHOW: Root-Cause Reports -> expand the top report -> incident_sources (5 sources in ONE incident)",
     "SHOW: the evidence chain - each source contributes a clue") -join "`r`n";
     Notes="This is the 'five sources, one incident' beat." },
  @{ Layout=2; Title="Live Demo (2) - Diagnose & Remember"; Body=@(
     "SAY: 'It weighed the layers and named the cause - the webapp under pressure, not the downstream.'",
     "SHOW: the Summary (root cause)",
     "SHOW: 'Similar past incidents' panel - INC0011880 at 72%, with the past fix",
     "SAY: 'It remembers. The engineer starts with the proven fix.'") -join "`r`n";
     Notes="The 'it has memory' beat - point at the confidence %." },
  @{ Layout=2; Title="Live Demo (3) - Act & Close"; Body=@(
     "SHOW: Recommended actions (optional: open the real ServiceNow ticket)",
     "SAY: 'It recommends the fix and files the ticket automatically.'",
     "CLOSE: 'All your logs in; one diagnosed incident, the proven fix, a ready ticket out - no manual triage. And it gets smarter every time.'") -join "`r`n";
     Notes="End strong on the one-liner." },
  @{ Layout=2; Title="Where Each Step Shows in the UI"; Body=@(
     "Ingest  ->  Events Explorer (+ Overview)",
     "Detect  ->  Findings",
     "Correlate / Diagnose / Precedent / Act  ->  Root-Cause Reports (expand a report)",
     "How correlation works  ->  Cases (one job, multiple sources, one timeline)",
     "Approvals queue  ->  Pending Approvals") -join "`r`n";
     Notes="If you show one screen: expand the top Root-Cause Report - it IS the pipeline." },
  @{ Layout=2; Title="The Wow Beats"; Body=@(
     "Five sources, one incident - automatic correlation",
     "It weighed the layers and picked the real cause",
     "It remembered how we fixed it (with a confidence %)",
     "It stays silent when unsure - no precedent below 60%") -join "`r`n";
     Notes="Land at least two of these explicitly." },
  @{ Layout=2; Title="Q&A - Likely Questions"; Body=@(
     "'Is the AI guessing?' -> No: detection & correlation are deterministic; AI only narrates cited evidence",
     "'Why not always 100% confident?' -> Honest: cautious with one pillar, confident when layers agree",
     "'Did you change the core?' -> No: sources plug in via config; the engine is untouched",
     "'How does it correlate?' -> shared job/host id + overlapping time window") -join "`r`n";
     Notes="Pre-rehearse these four." },
  @{ Layout=1; Title="Go Win the Room"; Body="Logs in -> diagnosed incident, proven fix, ready ticket - getting smarter every time.";
     Notes="Offer to show the live UI." }
)

$pp = New-Object -ComObject PowerPoint.Application
$pp.Visible = -1
$pres = $pp.Presentations.Add()
try { $pres.PageSetup.SlideSize = 17 } catch {}   # 17 = Widescreen (960 x 540 pt)
if ($thmx) { $pres.ApplyTemplate($thmx) }

$i = 1
foreach ($s in $slides) {
  $slide = $pres.Slides.Add($i, [int]$s.Layout)
  $slide.Shapes.Title.TextFrame.TextRange.Text = $s.Title
  if ($s.Body) { try { $slide.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text = $s.Body } catch {} }
  if ($s.Notes) { try { $slide.NotesPage.Shapes.Placeholders.Item(2).TextFrame.TextRange.Text = $s.Notes } catch {} }
  if ($s.Layout -ne 1) {
    try {
      $slide.HeadersFooters.Footer.Visible = -1
      $slide.HeadersFooters.Footer.Text = "Demo Flow - Architect's Guide   |   Internal"
      $slide.HeadersFooters.SlideNumber.Visible = -1
    } catch {}
  }
  $i++
}

if (Test-Path $OutPath) { Remove-Item $OutPath -Force }
$pres.SaveAs($OutPath, 24)
$pres.Close()
$pp.Quit()
Write-Output "Created: $OutPath  ($($slides.Count) slides, theme: $ThemeName)"
