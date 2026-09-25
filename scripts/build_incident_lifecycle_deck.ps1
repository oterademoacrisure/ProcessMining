# POINT 21 (Task #21): render the "Autonomous Incident Lifecycle" review deck (3 phases)
# as a PPTX via PowerPoint COM + PNG previews. Mirrors the Live Activity timeline.
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_incident_lifecycle_deck.ps1
param([string]$DocsDir = "$PSScriptRoot\..\docs")
$ErrorActionPreference = "Stop"
$DocsDir = [System.IO.Path]::GetFullPath($DocsDir)

function RGBv($r,$g,$b){ return [int]($r + $g*256 + $b*65536) }
# stage colors — consistent with the Live Activity UI
$NAVY=RGBv 31 78 121; $BLUE=RGBv 41 128 185; $INDIGO=RGBv 91 78 156; $ORANGE=RGBv 211 84 0
$AMBER=RGBv 214 137 16; $CRIMSON=RGBv 192 57 43; $GREEN=RGBv 31 157 87; $TEAL=RGBv 22 160 133
$GRAY=RGBv 127 140 141; $DARK=RGBv 33 37 41; $WHITE=RGBv 255 255 255

$pp = New-Object -ComObject PowerPoint.Application
$pp.Visible = -1
$W=960; $H=540
$pres = $pp.Presentations.Add()
$pres.PageSetup.SlideWidth=$W; $pres.PageSetup.SlideHeight=$H

function Add-Slide(){
  $idx=$pp.ActivePresentation.Slides.Count+1
  $sl=$pres.Slides.Add($idx,12)
  try { $sl.Background.Fill.Solid(); $sl.Background.Fill.ForeColor.RGB=$WHITE } catch {}
  return $sl
}
function Header($sl,$title,$sub){
  $bar=$sl.Shapes.AddShape(1,0,0,$W,54); $bar.Fill.Solid(); $bar.Fill.ForeColor.RGB=$NAVY; $bar.Line.Visible=0; $bar.Shadow.Visible=0
  $t=$sl.Shapes.AddTextbox(1,24,9,($W-48),28); $tr=$t.TextFrame.TextRange
  $tr.Text=$title; $tr.Font.Name="Segoe UI Semibold"; $tr.Font.Size=19; $tr.Font.Bold=-1; $tr.Font.Color.RGB=$WHITE
  if($sub){ $s=$sl.Shapes.AddTextbox(1,24,60,($W-48),20); $sr=$s.TextFrame.TextRange
    $sr.Text=$sub; $sr.Font.Name="Segoe UI"; $sr.Font.Size=11; $sr.Font.Italic=-1; $sr.Font.Color.RGB=$GRAY }
}
function Row($sl,$x,$y,$fill,$stage,$body){
  $c=$sl.Shapes.AddShape(5,$x,$y,110,22); $c.Fill.Solid(); $c.Fill.ForeColor.RGB=$fill; $c.Line.Visible=0; $c.Shadow.Visible=0
  $ct=$c.TextFrame; $ct.VerticalAnchor=3; $ct.MarginLeft=2; $ct.MarginRight=2; $ct.WordWrap=0
  $cr=$ct.TextRange; $cr.Text=$stage; $cr.Font.Name="Segoe UI"; $cr.Font.Size=9; $cr.Font.Bold=-1; $cr.Font.Color.RGB=$WHITE; $cr.ParagraphFormat.Alignment=2
  $t=$sl.Shapes.AddTextbox(1,($x+120),($y-2),760,26); $tr=$t.TextFrame.TextRange
  $tr.Text=$body; $tr.Font.Name="Segoe UI"; $tr.Font.Size=13; $tr.Font.Color.RGB=$DARK
}
function ValueBand($sl,$y,$color,$text){
  $b=$sl.Shapes.AddShape(5,24,$y,912,50); $b.Fill.Solid(); $b.Fill.ForeColor.RGB=$color; $b.Line.Visible=0; $b.Shadow.Visible=0
  $tf=$b.TextFrame; $tf.VerticalAnchor=3; $tf.MarginLeft=14; $tf.MarginRight=14; $tf.WordWrap=-1
  $tr=$tf.TextRange; $tr.Text=$text; $tr.Font.Name="Segoe UI"; $tr.Font.Size=12; $tr.Font.Bold=-1; $tr.Font.Color.RGB=$WHITE
}
function Card($sl,$x,$y,$w,$h,$color,$title,$lines){
  $c=$sl.Shapes.AddShape(5,$x,$y,$w,$h); $c.Fill.Solid(); $c.Fill.ForeColor.RGB=$color; $c.Line.Visible=0; $c.Shadow.Visible=-1
  $t=$sl.Shapes.AddTextbox(1,($x+14),($y+12),($w-28),26); $tr=$t.TextFrame.TextRange
  $tr.Text=$title; $tr.Font.Name="Segoe UI Semibold"; $tr.Font.Size=15; $tr.Font.Bold=-1; $tr.Font.Color.RGB=$WHITE
  $b=$sl.Shapes.AddTextbox(1,($x+14),($y+46),($w-28),($h-56)); $b.TextFrame.WordWrap=-1
  $br=$b.TextFrame.TextRange; $br.Text=$lines; $br.Font.Name="Segoe UI"; $br.Font.Size=11.5; $br.Font.Color.RGB=$WHITE
}

# ===================== SLIDE 1 — OVERVIEW =====================
$sl=Add-Slide
Header $sl "Autonomous Incident Lifecycle - live, explainable, self-improving" "One incident, three phases - as shown live in the Streamlit 'Live Activity' view"
Card $sl 24  92 288 300 $BLUE   "1 - Observe" "Ingest -> Detect -> Correlate`n`nSignals from Appian, Mule and infrastructure are automatically correlated into a single incident across systems.`n`nNo manual log-hunting."
Card $sl 336 92 288 300 $INDIGO "2 - Diagnose + Human Gate" "LLM root-cause analysis grounded in LIVE evidence AND our own PROVEN history.`n`nA human approves before anything changes."
Card $sl 648 92 288 300 $GREEN  "3 - Remediate + Learn" "One click -> automated fix, verified, audited in Jira, incident closed in ServiceNow.`n`nThe system learns for next time."
ValueBand $sl 410 $NAVY "Closed loop: detect -> diagnose (with history) -> approve -> remediate -> audit (Jira) -> resolve (ServiceNow) -> learn.   Explainable, governed, self-improving."

# ===================== SLIDE 2 — PHASE 1 =====================
$sl=Add-Slide
Header $sl "Phase 1 - Observe: Ingest, Detect & Correlate" "The platform watches every source and assembles one incident"
$y=92
Row $sl 40 $y $BLUE  "INGEST"    "Collected live telemetry signals from Appian, Mule and infrastructure"; $y+=40
Row $sl 40 $y $BLUE  "DETECT"    "Analyzed the signals and flagged anomalies"; $y+=40
Row $sl 40 $y $INDIGO "CORRELATE" "Connected the related signals into ONE incident across systems"
ValueBand $sl 470 $BLUE "Full-stack observability - cross-source signals auto-correlated into a single incident. Operators start from one clear picture, not scattered logs."

# ===================== SLIDE 3 — PHASE 2 =====================
$sl=Add-Slide
Header $sl "Phase 2 - Diagnose with live + historical context, then Await Approval" "Explainable AI root-cause, grounded in proven history - with a human gate"
$y=90
Row $sl 40 $y $INDIGO "DIAGNOSE"    "Analyzing the incident across the affected systems"; $y+=34
Row $sl 40 $y $INDIGO "DIAGNOSE"    "Searching our incident history for similar past cases"; $y+=34
Row $sl 40 $y $INDIGO "DIAGNOSE"    "Found proven historical resolution(s) to learn from"; $y+=34
Row $sl 40 $y $INDIGO "DIAGNOSE"    "AI reasoning over the live evidence AND historical data"; $y+=34
Row $sl 40 $y $INDIGO "DIAGNOSE"    "Root cause identified - recommendation backed by proven past fixes"; $y+=34
Row $sl 40 $y $ORANGE "SNOW_CREATE" "Incident ticket raised in ServiceNow (e.g. INC0010024)"; $y+=34
Row $sl 40 $y $AMBER  "APPROVAL"    "Awaiting human approval  (nothing runs until a person approves)"
ValueBand $sl 470 $INDIGO "Trust + speed: the AI cites LIVE evidence and our OWN proven resolutions with confidence - and a human approves before any change is made."

# ===================== SLIDE 4 — PHASE 3 =====================
$sl=Add-Slide
Header $sl "Phase 3 - After approval: Remediate, Audit, Resolve & Learn" "One click closes the loop - and the system gets smarter"
$y=90
Row $sl 40 $y $AMBER   "APPROVAL"     "Human approved"; $y+=34
Row $sl 40 $y $CRIMSON "VALIDATE"     "Safety-checked the proposed fix (allow-list)"; $y+=34
Row $sl 40 $y $CRIMSON "EXECUTE"      "Applied the recommended fix  (exit code 0)"; $y+=34
Row $sl 40 $y $CRIMSON "VERIFY"       "Confirmed the fix resolved the issue"; $y+=34
Row $sl 40 $y $GREEN   "JIRA_CREATE"  "Audit record logged in Jira (KAN-15, status Done)"; $y+=34
Row $sl 40 $y $ORANGE  "SNOW_RESOLVE" "ServiceNow incident closed (INC0010024, cites Jira KAN-15)"; $y+=34
Row $sl 40 $y $TEAL    "LEARN"        "Saved to institutional memory - the system just got smarter"
ValueBand $sl 470 $GREEN "Closed-loop, compliant, self-improving: fix applied + verified, cross-linked audit trail (Jira + ServiceNow), and every resolution becomes future knowledge."

# ===================== SAVE + EXPORT =====================
$pptx=Join-Path $DocsDir "INCIDENT_LIFECYCLE.pptx"
if(Test-Path $pptx){ Remove-Item $pptx -Force }
$pres.SaveAs($pptx,24)
for($i=1;$i -le 4;$i++){ $png=Join-Path $DocsDir ("INCIDENT_LIFECYCLE_$i.png"); if(Test-Path $png){Remove-Item $png -Force}; $pres.Slides.Item($i).Export($png,"PNG",1920,1080) }
$pres.Close(); $pp.Quit()
Write-Output "Wrote $pptx + PNG previews"
