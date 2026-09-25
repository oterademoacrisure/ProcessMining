# POINT 19: render the demo flow (closed-loop precedent RCA) via PowerPoint COM -> PNG.
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_demo_flow_diagram.ps1
param(
  [string]$Out  = "$PSScriptRoot\..\docs\DEMO_FLOW_DIAGRAM.png",
  [string]$Pptx = "$PSScriptRoot\..\docs\DEMO_FLOW_DIAGRAM.pptx"
)
$ErrorActionPreference = "Stop"
$Out  = [System.IO.Path]::GetFullPath($Out)
$Pptx = [System.IO.Path]::GetFullPath($Pptx)

function RGBv($r,$g,$b){ return [int]($r + $g*256 + $b*65536) }
$WHITE=RGBv 255 255 255; $SLATE=RGBv 71 85 105; $INK=RGBv 15 23 42; $NAVY=RGBv 30 64 110
$GREEN=RGBv 22 163 74; $ORANGE=RGBv 234 88 12; $PURPLE=RGBv 124 58 237
$BLUE=RGBv 37 99 235; $PINK=RGBv 219 39 119; $TEAL=RGBv 13 148 136

# Professional, widely-installed Windows fonts.
$FONT_HEAD = "Segoe UI Semibold"   # title + box labels
$FONT_BODY = "Segoe UI"            # annotations

$pp = New-Object -ComObject PowerPoint.Application
$pp.Visible = -1
$W=1160; $H=920
$pres = $pp.Presentations.Add()
$pres.PageSetup.SlideWidth = $W; $pres.PageSetup.SlideHeight = $H
$sl = $pres.Slides.Add(1,12)
$sl.Background.Fill.Solid(); $sl.Background.Fill.ForeColor.RGB = $WHITE

function Box($x,$y,$w,$h,$fill,$text,$fs=16){
  $s=$sl.Shapes.AddShape(5,$x,$y,$w,$h)       # rounded rectangle
  $s.Adjustments.Item(1)=0.18                  # softer corner radius
  $s.Fill.Solid(); $s.Fill.ForeColor.RGB=$fill
  $s.Line.Visible=0; $s.Shadow.Visible=-1
  $tf=$s.TextFrame; $tf.VerticalAnchor=3; $tf.WordWrap=-1
  $tr=$tf.TextRange; $tr.Text=$text; $tr.Font.Name=$FONT_HEAD; $tr.Font.Size=$fs
  $tr.Font.Color.RGB=$WHITE; $tr.Font.Bold=-1; $tr.ParagraphFormat.Alignment=2
}
function DownArrow($x,$y1,$y2){
  $l=$sl.Shapes.AddLine($x,$y1,$x,$y2); $l.Line.ForeColor.RGB=$NAVY; $l.Line.Weight=2.25; $l.Line.EndArrowheadStyle=4
}
function Note($x,$y,$w,$text){
  $t=$sl.Shapes.AddTextbox(1,$x,$y,$w,46)
  $tr=$t.TextFrame.TextRange; $tr.Text=$text; $tr.Font.Name=$FONT_BODY; $tr.Font.Size=13; $tr.Font.Color.RGB=$SLATE
  $tr.ParagraphFormat.Alignment=1
}
function LoopSeg($x1,$y1,$x2,$y2,$arrow){
  $l=$sl.Shapes.AddLine($x1,$y1,$x2,$y2); $l.Line.ForeColor.RGB=$TEAL; $l.Line.Weight=3
  if($arrow){ $l.Line.EndArrowheadStyle=4 }
}

$t=$sl.Shapes.AddTextbox(1,44,18,1060,48)
$tr=$t.TextFrame.TextRange; $tr.Text="Precedent RCA Demo: Closed-Loop Flow"
$tr.Font.Name=$FONT_HEAD; $tr.Font.Size=30; $tr.Font.Bold=-1; $tr.Font.Color.RGB=$INK

$bw=500; $bh=60; $bx=[int](($W-$bw)/2); $cx=$bx+[int]($bw/2)
$ys=@(82,172,262,352,442,532,622,712,802)
$cols=@($NAVY,$GREEN,$GREEN,$ORANGE,$PURPLE,$BLUE,$PINK,$PURPLE,$TEAL)
$txt=@(
 "Telemetry Sources: Appian, Mule, Prometheus, Fluentd",
 "Ingest Logs",
 "Detect Findings",
 "Diagnose: AI + Precedent Search",
 "Create ServiceNow Ticket",
 "Operator Approves the Fix",
 "Remediate: Validate, Execute, Verify",
 "Resolve ServiceNow Ticket",
 "Learn: Capture New Precedent"
)
$notes=@(
 "Log files",
 "Stored in PostgreSQL",
 "Stored in PostgreSQL",
 "FAISS + PostgreSQL hybrid search (min 0.60); Azure OpenAI",
 "ServiceNow REST API",
 "Streamlit UI",
 "LangGraph (simulated execution)",
 "Marked Resolved in ServiceNow",
 "Saved to PostgreSQL + FAISS"
)
for($i=0;$i -lt $ys.Count;$i++){
  Box $bx $ys[$i] $bw $bh $cols[$i] $txt[$i] 16
  Note ($bx+$bw+24) ($ys[$i]+9) 300 $notes[$i]
  if($i -gt 0){ DownArrow $cx ($ys[$i-1]+$bh) $ys[$i] }
}

# Learning loop: LEARN (box 9) -> left -> up -> into DIAGNOSE (box 4)
$y9=$ys[8]+[int]($bh/2); $y4=$ys[3]+[int]($bh/2); $lx=200
LoopSeg $bx $y9 $lx $y9 $false
LoopSeg $lx $y9 $lx $y4 $false
LoopSeg $lx $y4 $bx $y4 $true
$lt=$sl.Shapes.AddTextbox(1,44,([int](($y4+$y9)/2)-38),150,80)
$ltr=$lt.TextFrame.TextRange
$ltr.Text="Learning Loop" + [char]13 + "the new precedent feeds the next diagnosis"
$ltr.Font.Name=$FONT_HEAD; $ltr.Font.Size=13; $ltr.Font.Bold=-1; $ltr.Font.Color.RGB=$TEAL; $ltr.ParagraphFormat.Alignment=2

if(Test-Path $Out){ Remove-Item $Out -Force }
$sl.Export($Out,"PNG",[int]($W*2),[int]($H*2))
if(Test-Path $Pptx){ Remove-Item $Pptx -Force }
$pres.SaveAs($Pptx, 24)          # 24 = ppSaveAsOpenXMLPresentation (.pptx, editable)
$pres.Close(); $pp.Quit()
Write-Output "PNG:  $Out"
Write-Output "PPTX: $Pptx"
