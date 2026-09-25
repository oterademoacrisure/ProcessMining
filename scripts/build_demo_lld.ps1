# POINT 20: render the DEMO Low-Level-Design deck (Part A own-MCP + Part B Atlassian-MCP)
# as a PPTX via PowerPoint COM, and export each slide to PNG for review.
# Colors are SEMANTIC (see $LEG legend, drawn on every slide).
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_demo_lld.ps1
param([string]$DocsDir = "$PSScriptRoot\..\docs")
$ErrorActionPreference = "Stop"
$DocsDir = [System.IO.Path]::GetFullPath($DocsDir)

function RGBv($r,$g,$b){ return [int]($r + $g*256 + $b*65536) }
# ---- semantic palette (meaning fixed by the legend) ----
$BLUE   = RGBv 41 128 185     # Ingestion & Detection
$INDIGO = RGBv 91 78 156      # AI Diagnosis (LLM RCA)
$TEAL   = RGBv 22 160 133     # Precedent Memory (learning)
$AMBER  = RGBv 214 137 16     # Human Approval (HITL)
$CRIMSON= RGBv 192 57 43      # Remediation (LangGraph)
$ORANGE = RGBv 211 84 0       # ServiceNow (ITSM)
$GREEN  = RGBv 31 157 87      # Jira audit via MCP
$NAVY   = RGBv 31 78 121      # Orchestration / transport / control
# ---- neutrals ----
$GRAY = RGBv 127 140 141; $DARK = RGBv 33 37 41; $WHITE = RGBv 255 255 255
$LTEAL = RGBv 224 245 241; $LGRAY = RGBv 244 246 248

# legend definition (drawn on every slide so each color has documented meaning)
$LEG=@(
 @{c=$BLUE;   t="Ingestion & Detection"},
 @{c=$INDIGO; t="AI Diagnosis (LLM RCA)"},
 @{c=$TEAL;   t="Precedent Memory (learning)"},
 @{c=$AMBER;  t="Human Approval (HITL)"},
 @{c=$CRIMSON;t="Remediation (LangGraph)"},
 @{c=$ORANGE; t="ServiceNow (ITSM)"},
 @{c=$GREEN;  t="Jira audit via MCP"},
 @{c=$NAVY;   t="Orchestration / transport"}
)

$pp = New-Object -ComObject PowerPoint.Application
$pp.Visible = -1
$W = 960; $H = 540
$pres = $pp.Presentations.Add()
$pres.PageSetup.SlideWidth = $W
$pres.PageSetup.SlideHeight = $H

function Add-Slide(){
  $idx = $pp.ActivePresentation.Slides.Count + 1
  $sl = $pres.Slides.Add($idx,12)
  try { $sl.Background.Fill.Solid(); $sl.Background.Fill.ForeColor.RGB = $WHITE } catch {}
  return $sl
}
function Header($sl,$title,$subtitle){
  $bar = $sl.Shapes.AddShape(1,0,0,$W,50); $bar.Fill.Solid(); $bar.Fill.ForeColor.RGB=$NAVY; $bar.Line.Visible=0; $bar.Shadow.Visible=0
  $t = $sl.Shapes.AddTextbox(1,24,8,($W-48),34); $tr=$t.TextFrame.TextRange
  $tr.Text=$title; $tr.Font.Name="Segoe UI Semibold"; $tr.Font.Size=19; $tr.Font.Bold=-1; $tr.Font.Color.RGB=$WHITE
  if($subtitle){
    $s=$sl.Shapes.AddTextbox(1,24,54,($W-48),20); $sr=$s.TextFrame.TextRange
    $sr.Text=$subtitle; $sr.Font.Name="Segoe UI"; $sr.Font.Size=11; $sr.Font.Italic=-1; $sr.Font.Color.RGB=$GRAY
  }
}
function Box($sl,$x,$y,$w,$h,$fill,$text,$fs=12,$txt=$WHITE){
  $s=$sl.Shapes.AddShape(5,$x,$y,$w,$h); $s.Fill.Solid(); $s.Fill.ForeColor.RGB=$fill
  $s.Line.Visible=0; $s.Shadow.Visible=-1
  $tf=$s.TextFrame; $tf.VerticalAnchor=3; $tf.WordWrap=-1
  $tf.MarginLeft=4; $tf.MarginRight=4; $tf.MarginTop=2; $tf.MarginBottom=2
  $tr=$tf.TextRange; $tr.Text=$text; $tr.Font.Name="Segoe UI"; $tr.Font.Size=$fs
  $tr.Font.Color.RGB=$txt; $tr.Font.Bold=-1; $tr.ParagraphFormat.Alignment=2
}
function Lite($sl,$x,$y,$w,$h,$border,$text,$fs=10){
  $s=$sl.Shapes.AddShape(5,$x,$y,$w,$h); $s.Fill.Solid(); $s.Fill.ForeColor.RGB=$WHITE
  $s.Line.Visible=-1; $s.Line.ForeColor.RGB=$border; $s.Line.Weight=1.25; $s.Shadow.Visible=0
  $tf=$s.TextFrame; $tf.VerticalAnchor=3; $tf.WordWrap=-1
  $tf.MarginLeft=3; $tf.MarginRight=3; $tf.MarginTop=1; $tf.MarginBottom=1
  $tr=$tf.TextRange; $tr.Text=$text; $tr.Font.Name="Segoe UI"; $tr.Font.Size=$fs
  $tr.Font.Color.RGB=$DARK; $tr.ParagraphFormat.Alignment=2
}
function GroupBox($sl,$x,$y,$w,$h,$fill,$border,$title,$tcolor){
  $s=$sl.Shapes.AddShape(5,$x,$y,$w,$h); $s.Fill.Solid(); $s.Fill.ForeColor.RGB=$fill
  $s.Line.Visible=-1; $s.Line.ForeColor.RGB=$border; $s.Line.Weight=1.75; $s.Shadow.Visible=0
  $t=$sl.Shapes.AddTextbox(1,($x+10),($y+5),($w-20),20); $tr=$t.TextFrame.TextRange
  $tr.Text=$title; $tr.Font.Name="Segoe UI Semibold"; $tr.Font.Size=12; $tr.Font.Bold=-1; $tr.Font.Color.RGB=$tcolor
}
function Arrow($sl,$x1,$y1,$x2,$y2,$color=$NAVY,$dash=$false,$wt=2.0){
  $l=$sl.Shapes.AddLine($x1,$y1,$x2,$y2); $l.Line.ForeColor.RGB=$color; $l.Line.Weight=$wt
  $l.Line.EndArrowheadStyle=2; $l.Line.EndArrowheadLength=2; $l.Line.EndArrowheadWidth=2
  if($dash){ $l.Line.DashStyle=4 }
}
function Lbl($sl,$x,$y,$w,$text,$color=$DARK,$fs=9){
  $t=$sl.Shapes.AddTextbox(1,$x,$y,$w,16); $tr=$t.TextFrame.TextRange
  $tr.Text=$text; $tr.Font.Name="Segoe UI"; $tr.Font.Size=$fs; $tr.Font.Italic=-1; $tr.Font.Color.RGB=$color
  $tr.ParagraphFormat.Alignment=2
}
function Badge($sl,$x,$y,$n){
  $s=$sl.Shapes.AddShape(9,$x,$y,24,22); $s.Fill.Solid(); $s.Fill.ForeColor.RGB=$DARK; $s.Line.ForeColor.RGB=$WHITE; $s.Line.Weight=1.25
  $tf=$s.TextFrame; $tf.VerticalAnchor=3; $tf.WordWrap=0; $tf.MarginLeft=0; $tf.MarginRight=0
  $tr=$tf.TextRange; $tr.Text="$n"; $tr.Font.Name="Segoe UI"; $tr.Font.Size=10; $tr.Font.Bold=-1; $tr.Font.Color.RGB=$WHITE; $tr.ParagraphFormat.Alignment=2
}
function Legend($sl,$x0,$y,$items,$per=4,$sw=228){
  $cap=$sl.Shapes.AddTextbox(1,$x0,($y-18),200,16); $cr=$cap.TextFrame.TextRange
  $cr.Text="Color legend"; $cr.Font.Name="Segoe UI Semibold"; $cr.Font.Size=9; $cr.Font.Bold=-1; $cr.Font.Color.RGB=$DARK
  for($i=0;$i -lt $items.Count;$i++){
    $row=[int]([Math]::Floor($i/$per)); $col=$i%$per
    $x=$x0+$col*$sw; $yy=$y+$row*20
    $s=$sl.Shapes.AddShape(5,$x,$yy,13,13); $s.Fill.Solid(); $s.Fill.ForeColor.RGB=$items[$i].c; $s.Line.Visible=0; $s.Shadow.Visible=0
    $t=$sl.Shapes.AddTextbox(1,($x+17),($yy-3),($sw-22),18); $tr=$t.TextFrame.TextRange
    $tr.Text=$items[$i].t; $tr.Font.Name="Segoe UI"; $tr.Font.Size=8; $tr.Font.Color.RGB=$DARK; $tr.ParagraphFormat.Alignment=1
  }
}

# ============================== SLIDE 1 : END-TO-END ==============================
$sl = Add-Slide
Header $sl "Demo LLD - End-to-End Closed Loop" "Detect -> Diagnose (with precedent) -> human approve -> remediate -> audit (Jira) -> resolve (ServiceNow) -> learn"

$ty=92; $bw=168; $bh=58; $xs=@(24,210,396,582,768)
Box $sl $xs[0] $ty $bw $bh $BLUE   "Log Sources`nAppian / Mule / Infra" 12
Box $sl $xs[1] $ty $bw $bh $BLUE   "Ingest`n-> event_log" 12
Box $sl $xs[2] $ty $bw $bh $BLUE   "Detect`n-> finding" 12
Box $sl $xs[3] $ty $bw $bh $INDIGO "Correlate`nincident_grouper" 12
Box $sl $xs[4] $ty $bw $bh $INDIGO "Diagnose`nLLM RCA Investigator" 12
for($i=0;$i -lt 5;$i++){ Badge $sl ($xs[$i]-6) ($ty-6) ($i+1) }
for($i=0;$i -lt 4;$i++){ Arrow $sl ($xs[$i]+$bw) ($ty+$bh/2) ($xs[$i+1]) ($ty+$bh/2) $GRAY }

$gx=300; $gy=182; $gw=540; $gh=148
GroupBox $sl $gx $gy $gw $gh $LTEAL $TEAL "Precedent Memory - hybrid retrieval (>= 0.60)" $TEAL
Lite $sl ($gx+12)  ($gy+30) 168 38 $TEAL "Azure Embedder`ntext-embedding-3-small" 9
Lite $sl ($gx+190) ($gy+30) 152 38 $TEAL "FAISS index`n(semantic arm)" 9
Lite $sl ($gx+352) ($gy+30) 176 38 $TEAL "Postgres FTS`n(keyword arm)" 9
Lite $sl ($gx+12)  ($gy+76) 256 48 $TEAL "RRF fusion -> noisy-OR`nconfidence >= 0.60" 9
Lite $sl ($gx+278) ($gy+76) 250 48 $TEAL "HistoricalIncident store`napproved / rejected outcome" 9

Lite $sl 560 150 200 28 $INDIGO "Azure OpenAI (GPT) - RCA synthesis" 9
Arrow $sl 760 156 778 150 $INDIGO
Arrow $sl 800 150 800 182 $TEAL
Lbl $sl 803 158 130 "find_precedents()" $TEAL 8
Arrow $sl 852 150 852 388 $NAVY
Lbl $sl 786 304 150 "-> root_cause_report" $NAVY 8

$by=388
Box $sl $xs[4] $by $bw $bh $INDIGO  "root_cause_report`n+ precedent block" 12
Box $sl $xs[3] $by $bw $bh $ORANGE  "ServiceNow Sink`nreal INC ticket" 12
Box $sl $xs[2] $by $bw $bh $AMBER   "HITL Approval`n(Streamlit)" 12
Box $sl $xs[1] $by $bw $bh $CRIMSON "LangGraph Remediation`nvalidate->execute->verify" 11
Box $sl $xs[0] $by $bw $bh $GREEN   "MCP Layer (A/B switch)`nJira ticket -> Done" 12
$lbls6=@(6,7,8,9,10); $order=@(4,3,2,1,0)
for($i=0;$i -lt 5;$i++){ Badge $sl ($xs[$order[$i]]-6) ($by-6) $lbls6[$i] }
for($i=0;$i -lt 4;$i++){ Arrow $sl ($xs[$order[$i]]) ($by+$bh/2) ($xs[$order[$i+1]]+$bw) ($by+$bh/2) $GRAY }

Box $sl 24 250 168 52 $TEAL   "capture_precedent`n-> learn (approved)" 11
Box $sl 24 186 168 52 $ORANGE "ServiceNow Resolved`nclose-notes cite Jira key" 11
Arrow $sl 108 388 108 302 $TEAL
Arrow $sl 108 250 108 238 $ORANGE
Arrow $sl 192 264 300 256 $TEAL $true
Lbl $sl 196 244 110 "feedback: re-embed" $TEAL 8

Lbl $sl 300 346 540 "Verified => Jira Done + ServiceNow Resolved (cross-linked).   Failed/unverified => Jira stays Open." $GRAY 9
Legend $sl 24 500 $LEG 4 228

# ============================== SLIDE 2 : MCP A vs B ==============================
$sl2 = Add-Slide
Header $sl2 "MCP Integration LLD - Part A (own server) vs Part B (Atlassian hosted)" "One config line (jira_mcp_server: own | atlassian) swaps the backend - pipeline code unchanged"

$cy=84; $cw=212; $ch=42
Box $sl2 24  $cy $cw $ch $CRIMSON "run_workflow`n(fix verified)" 11
Box $sl2 252 $cy $cw $ch $GREEN   "jira_feedback`ncreate_jira_ticket()" 11
Box $sl2 480 $cy $cw $ch $GREEN   "jira_mcp_client`nfile_ticket(mark_done)" 11
Box $sl2 708 $cy $cw $ch $NAVY    "switch`nconfig.jira_mcp_server" 11
Arrow $sl2 236 ($cy+$ch/2) 252 ($cy+$ch/2) $GRAY
Arrow $sl2 464 ($cy+$ch/2) 480 ($cy+$ch/2) $GRAY
Arrow $sl2 692 ($cy+$ch/2) 708 ($cy+$ch/2) $GRAY
Arrow $sl2 760 ($cy+$ch) 250 166 $NAVY
Arrow $sl2 824 ($cy+$ch) 710 166 $NAVY
Lbl $sl2 70  148 280 "own  (default - unattended)" $GREEN 9
Lbl $sl2 560 148 280 "atlassian  (official hosted)" $ORANGE 9

# ----- Part A column -----
$ax=24; $aw=448; $ay=168; $agh=262
GroupBox $sl2 $ax $ay $aw $agh $LGRAY $NAVY "Part A - Own hosted MCP" $NAVY
$col=$ax+24; $cwid=$aw-48
Box $sl2 $col ($ay+30)  $cwid 38 $NAVY  "FastMCP Client -> HTTP`nhttp://127.0.0.1:8090/mcp" 11
Arrow $sl2 ($col+$cwid/2) ($ay+68) ($col+$cwid/2) ($ay+76) $GRAY
Box $sl2 $col ($ay+76)  $cwid 38 $GREEN "jira_server.py (our FastMCP server)" 11
Arrow $sl2 ($col+$cwid/2) ($ay+114) ($col+$cwid/2) ($ay+122) $GRAY
Box $sl2 $col ($ay+122) $cwid 38 $GREEN "tools: create_issue -> transition_issue('Done')" 10
Arrow $sl2 ($col+$cwid/2) ($ay+160) ($col+$cwid/2) ($ay+168) $GRAY
Box $sl2 $col ($ay+168) $cwid 38 $GREEN "Jira REST v2 - Basic auth (email + API token)" 10
Lite $sl2 $col ($ay+212) $cwid 44 $NAVY "Auth: API token (.env / Key Vault)  |  Browser: never`nReturns: result.data" 9

# ----- Part B column -----
$bx=488; $bw2=448; $by2=168; $bgh=262
GroupBox $sl2 $bx $by2 $bw2 $bgh $LGRAY $NAVY "Part B - Atlassian hosted MCP" $NAVY
$col2=$bx+24; $cwid2=$bw2-48
Box $sl2 $col2 ($by2+30)  $cwid2 38 $NAVY  "FastMCP Client -> Streamable HTTP`nhttps://mcp.atlassian.com/v1/mcp" 10
Arrow $sl2 ($col2+$cwid2/2) ($by2+68) ($col2+$cwid2/2) ($by2+76) $GRAY
Box $sl2 $col2 ($by2+76)  $cwid2 38 $GREEN "Atlassian Remote MCP (31 tools)" 11
Arrow $sl2 ($col2+$cwid2/2) ($by2+114) ($col2+$cwid2/2) ($by2+122) $GRAY
Box $sl2 $col2 ($by2+122) $cwid2 38 $GREEN "createJiraIssue -> getTransitions -> transition {id:31}=Done" 9
Arrow $sl2 ($col2+$cwid2/2) ($by2+160) ($col2+$cwid2/2) ($by2+168) $GRAY
Box $sl2 $col2 ($by2+168) $cwid2 38 $GREEN "Jira Cloud" 11
Lite $sl2 $col2 ($by2+212) $cwid2 44 $GREEN "Auth: OAuth (dynamic reg), token in OS keyring, auto-refresh`nBrowser: once (authorize)  |  Returns: content[].text JSON" 9

# legend + shared footer
Legend $sl2 24 440 @($LEG[4],$LEG[6],$LEG[7]) 3 230
$fy=462
$bar=$sl2.Shapes.AddShape(5,24,$fy,912,60); $bar.Fill.Solid(); $bar.Fill.ForeColor.RGB=$NAVY; $bar.Line.Visible=0; $bar.Shadow.Visible=0
$tf=$bar.TextFrame; $tf.VerticalAnchor=3; $tr=$tf.TextRange
$tr.Text="Both -> project KAN   |   verified => Jira Done + ServiceNow Resolved (close-notes cite Jira key)   |   failed/unverified => Jira stays Open`nAutomation: Part B needs a one-time browser OAuth (then silent refresh) -> NOT truly zero-touch.  Part A (API token in Key Vault) is the fully unattended path."
$tr.Font.Name="Segoe UI"; $tr.Font.Size=10; $tr.Font.Bold=-1; $tr.Font.Color.RGB=$WHITE; $tr.ParagraphFormat.Alignment=2

# ============================== SLIDE 3 : OBSERVABILITY (Task #21) ==============================
$sl3 = Add-Slide
Header $sl3 "Observability LLD - Live Activity timeline + OpenTelemetry / Langfuse" "Task #21: instrument ONCE at each stage -> fan out to three audiences (fail-safe, flag-gated)"

# left: the pipeline stages (each one emits)
GroupBox $sl3 24 92 236 316 $LGRAY $NAVY "Pipeline stages (each emits)" $NAVY
Lite $sl3 40 124 204 268 $NAVY ("INGEST`nDETECT`nCORRELATE`nDIAGNOSE  (+ AI reasoning)`nSNOW_CREATE`nAPPROVAL`nVALIDATE`nEXECUTE`nVERIFY`nJIRA_CREATE`nSNOW_RESOLVE`nLEARN") 11

# center: the single instrumentation hook
Box $sl3 296 208 214 96 $INDIGO "ProgressEmitter`n(instrument once)`n`nprogress.stage() / generation()" 12
Arrow $sl3 260 250 296 250 $GRAY

# right: three fan-out consumers
Box $sl3 556 98  380 92 $GREEN  "pipeline_event (Postgres)  ->  Streamlit 'Live Activity'`nlive timeline - colored status - working banner`n[ OPERATORS ]" 11
Box $sl3 556 208 380 92 $NAVY   "OpenTelemetry spans  ->  APM`nAzure Monitor / Grafana Tempo / Jaeger`n[ SRE / OPS ]" 11
Box $sl3 556 318 380 92 $TEAL   "Langfuse  (via OTLP - no SDK)`nLLM prompt - tokens - cost - quality / evals`n[ AI ENGINEERS ]" 11
Arrow $sl3 510 250 556 144 $GREEN
Arrow $sl3 510 252 556 252 $NAVY
Arrow $sl3 510 254 556 360 $TEAL
Lbl $sl3 512 150 120 "always on" $GREEN 8
Lbl $sl3 512 300 120 "gen_ai.* / OTLP" $TEAL 8

$bar3=$sl3.Shapes.AddShape(5,24,432,912,54); $bar3.Fill.Solid(); $bar3.Fill.ForeColor.RGB=$NAVY; $bar3.Line.Visible=0; $bar3.Shadow.Visible=0
$tf3=$bar3.TextFrame; $tf3.VerticalAnchor=3; $tr3=$tf3.TextRange
$tr3.Text="One instrumentation -> three audiences.  Fail-safe (a telemetry error NEVER blocks the pipeline) - flag-gated - OTLP standard (no vendor lock-in).`nThe live timeline needs NO extra dependencies; OpenTelemetry + Langfuse are optional add-ons."
$tr3.Font.Name="Segoe UI"; $tr3.Font.Size=10; $tr3.Font.Bold=-1; $tr3.Font.Color.RGB=$WHITE; $tr3.ParagraphFormat.Alignment=2

# ============================== SAVE + EXPORT ==============================
$pptx = Join-Path $DocsDir "DEMO_LLD.pptx"
if(Test-Path $pptx){ Remove-Item $pptx -Force }
$pres.SaveAs($pptx,24)
foreach($i in 1..3){ $png = Join-Path $DocsDir "DEMO_LLD_$i.png"; if(Test-Path $png){ Remove-Item $png -Force }; $pres.Slides.Item($i).Export($png,"PNG",1920,1080) }
$pres.Close()
$pp.Quit()
Write-Output "Wrote $pptx (3 slides) + PNG previews"
