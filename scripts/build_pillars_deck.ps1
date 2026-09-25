# POINT: render the "Architectural Pillars" business deck (PPTX + PNG previews) via PowerPoint COM.
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_pillars_deck.ps1
param([string]$DocsDir = "$PSScriptRoot\..\docs")
$ErrorActionPreference = "Stop"
$DocsDir = [System.IO.Path]::GetFullPath($DocsDir)

function RGBv($r,$g,$b){ return [int]($r + $g*256 + $b*65536) }
$NAVY=RGBv 31 78 121; $WHITE=RGBv 255 255 255; $GRAY=RGBv 120 130 140; $DARK=RGBv 33 37 41
$LGRAY=RGBv 244 246 248
# 9 pillar colors
$C=@((RGBv 31 78 121),(RGBv 41 128 185),(RGBv 22 160 133),(RGBv 91 78 156),(RGBv 214 137 16),(RGBv 192 57 43),(RGBv 17 120 100),(RGBv 125 60 152),(RGBv 52 73 94))

$pp = New-Object -ComObject PowerPoint.Application
$pp.Visible = -1
$W=960; $H=540
$pres = $pp.Presentations.Add()
$pres.PageSetup.SlideWidth=$W; $pres.PageSetup.SlideHeight=$H

function Add-Slide(){ $i=$pp.ActivePresentation.Slides.Count+1; $sl=$pres.Slides.Add($i,12); try{$sl.Background.Fill.Solid();$sl.Background.Fill.ForeColor.RGB=$WHITE}catch{}; return $sl }
function TB($sl,$x,$y,$w,$h,$text,$fs,$color,$bold,$align,$italic){
  $t=$sl.Shapes.AddTextbox(1,$x,$y,$w,$h); $tf=$t.TextFrame; $tf.WordWrap=-1; $tr=$tf.TextRange
  $tr.Text=$text; $tr.Font.Name="Segoe UI"; $tr.Font.Size=[single]$fs; $tr.Font.Color.RGB=[int]$color
  if($bold){$tr.Font.Bold=-1}; if($italic){$tr.Font.Italic=-1}; $tr.ParagraphFormat.Alignment=$align
}
function Header($sl,$title){
  $bar=$sl.Shapes.AddShape(1,0,0,$W,50); $bar.Fill.Solid(); $bar.Fill.ForeColor.RGB=$NAVY; $bar.Line.Visible=0; $bar.Shadow.Visible=0
  TB $sl 24 9 ($W-48) 30 $title 19 $WHITE $true 1 $false
}
function Card($sl,$x,$y,$w,$h,$color,$title,$body){
  $s=$sl.Shapes.AddShape(5,$x,$y,$w,$h); $s.Fill.Solid(); $s.Fill.ForeColor.RGB=$color; $s.Line.Visible=0; $s.Shadow.Visible=-1
  TB $sl ($x+14) ($y+10) ($w-28) 40 $title 14 $WHITE $true 1 $false
  TB $sl ($x+14) ($y+48) ($w-28) ($h-56) $body 11 $WHITE $false 1 $false
}

# ============ SLIDE 1 : TITLE ============
$sl=Add-Slide
$bar=$sl.Shapes.AddShape(1,0,150,$W,150); $bar.Fill.Solid(); $bar.Fill.ForeColor.RGB=$NAVY; $bar.Line.Visible=0; $bar.Shadow.Visible=0
TB $sl 40 172 ($W-80) 50 "ServerOps - Architectural Pillars" 30 $WHITE $true 1 $false
TB $sl 40 236 ($W-80) 30 "AI-driven, MCP-enabled process mining and autonomous incident remediation" 14 (RGBv 210 225 245) $false 1 $true
TB $sl 40 330 ($W-80) 30 "Open standards  -  Source-agnostic  -  Explainable AI  -  Governed automation  -  Full observability  -  Self-improving" 12 $GRAY $false 1 $true
TB $sl 40 470 ($W-80) 24 "Detect -> Diagnose (with precedent) -> Approve -> Remediate -> Audit -> Resolve -> Learn" 11 $NAVY $true 1 $false

# ============ SLIDE 2 : PILLARS GRID (3x3) ============
$sl=Add-Slide
Header $sl "Architectural Pillars - what makes it enterprise-ready"
$titles=@(
 "MCP-Enabled, Open Standard",
 "Connects Any Log Source",
 "Scalable, Normalized Data",
 "Precedent-Grounded AI RCA",
 "Governed Automation (HITL)",
 "Closed-Loop Remediation & Audit",
 "Data & RCA Reporting",
 "Live Activity Capture",
 "Enterprise Observability")
$bodies=@(
 "Integrations over the open MCP protocol - swap backends (e.g. Jira own vs Atlassian) via one config line. No vendor lock-in.",
 "Appian, Mule, Prometheus, Fluentd and more - add a new source by config, not code. Ready to plug into your existing log estate.",
 "Heterogeneous logs unified into one multi-tenant data model on managed Postgres - built to scale across teams and volume.",
 "Root cause diagnosed by AI grounded in your OWN proven past fixes (FAISS vector memory + ServiceNow history) - explainable, not guesswork.",
 "Nothing runs without human approval; only safe, allow-listed actions execute - automation with guardrails, safe for production.",
 "Validate -> execute -> verify -> resolve, automatically; every action cross-linked in Jira + ServiceNow - compliance & change-ready.",
 "Clear root-cause reports, searchable precedent memory and operator dashboards - insight, not raw logs.",
 "Real-time, Claude-style stage timeline - operators see exactly what the pipeline is doing right now, live.",
 "Instrument once, fan out: OpenTelemetry traces for SREs and Langfuse for LLM cost & quality - full transparency.")
$cw=293; $ch=134; $gx=16; $gy=14; $x0=24; $y0=66
for($i=0;$i -lt 9;$i++){
  $col=$i%3; $row=[int]([Math]::Floor($i/3))
  $x=$x0+$col*($cw+$gx); $y=$y0+$row*($ch+$gy)
  Card $sl $x $y $cw $ch $C[$i] ("$($i+1). "+$titles[$i]) $bodies[$i]
}

# ============ SLIDE 3 : BUSINESS OUTCOMES ============
$sl=Add-Slide
Header $sl "Why it matters - business outcomes"
$ot=@("Faster MTTR","Lower risk","No lock-in","Compliance-ready","Future-proof","Gets smarter")
$ob=@(
 "Detection to verified fix with minimal human effort - incidents resolved in a fraction of the time.",
 "Human-approved, allow-listed, fail-safe by design - autonomy without losing control.",
 "Built on open standards (MCP, OpenTelemetry) - swap tools and backends without re-engineering.",
 "Cross-linked Jira + ServiceNow audit trail for every automated action - audit and change-management evidence built in.",
 "Pluggable sources and backends - onboard new systems and tools by configuration as the estate grows.",
 "Every resolved incident becomes searchable precedent - accuracy compounds over time.")
$cw=293; $ch=150; $gx=16; $gy=16; $x0=24; $y0=76
for($i=0;$i -lt 6;$i++){
  $col=$i%3; $row=[int]([Math]::Floor($i/3))
  $x=$x0+$col*($cw+$gx); $y=$y0+$row*($ch+$gy)
  Card $sl $x $y $cw $ch $C[$i] $ot[$i] $ob[$i]
}
TB $sl 24 500 ($W-48) 24 "One platform: observe every source, diagnose with memory, remediate with governance, prove it with a cross-system audit trail." 11 $NAVY $true 1 $true

# ============ SAVE + EXPORT ============
$pptx=Join-Path $DocsDir "ARCHITECTURE_PILLARS.pptx"
if(Test-Path $pptx){ Remove-Item $pptx -Force }
$pres.SaveAs($pptx,24)
for($i=1;$i -le 3;$i++){ $png=Join-Path $DocsDir ("ARCHITECTURE_PILLARS_$i.png"); if(Test-Path $png){Remove-Item $png -Force}; $pres.Slides.Item($i).Export($png,"PNG",1920,1080) }
$pres.Close(); $pp.Quit()
Write-Output "Wrote $pptx (3 slides) + PNG previews"
