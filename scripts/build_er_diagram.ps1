# POINT: draw the Data-Model (ER) diagram via PowerPoint COM -> PNG (offline).
# Larger fonts so it stays legible when embedded in the docx (landscape page).
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_er_diagram.ps1
param([string]$PngPath = "$PSScriptRoot\..\docs\LOW_LEVEL_DESIGN_diagram_1.png")
$ErrorActionPreference = "Stop"
$PngPath = [System.IO.Path]::GetFullPath($PngPath)

function RGBv($r,$g,$b){ return [int]($r + $g*256 + $b*65536) }
$NAVY=RGBv 31 78 121; $PURPLE=RGBv 124 58 237; $WHITE=RGBv 255 255 255; $GRAY=RGBv 110 120 140

$pp = New-Object -ComObject PowerPoint.Application
$pp.Visible = -1
$pres = $pp.Presentations.Add()
try { $pres.PageSetup.SlideSize = 17 } catch {}   # 960x540
$slide = $pres.Slides.Add(1, 12)
try { $slide.Background.Fill.Solid(); $slide.Background.Fill.ForeColor.RGB = $WHITE } catch {}

function Entity($x,$y,$text,$fill){
  $s = $slide.Shapes.AddShape(5,$x,$y,290,50)
  $s.Fill.Solid(); $s.Fill.ForeColor.RGB=$fill; $s.Line.ForeColor.RGB=$WHITE; $s.Line.Weight=1; $s.Shadow.Visible=-1
  $tf=$s.TextFrame; $tf.VerticalAnchor=3; $tf.WordWrap=-1
  $tr=$tf.TextRange; $tr.Text=$text; $tr.Font.Name="Consolas"; $tr.Font.Size=16; $tr.Font.Bold=-1
  $tr.Font.Color.RGB=$WHITE; $tr.ParagraphFormat.Alignment=2
}
function Rel($x1,$y1,$x2,$y2,$label){
  $ln=$slide.Shapes.AddLine($x1,$y1,$x2,$y2); $ln.Line.ForeColor.RGB=$GRAY; $ln.Line.Weight=1.5
  $t=$slide.Shapes.AddTextbox(1, ($x1+8), (($y1+$y2)/2 - 9), 250, 18)
  $tr=$t.TextFrame.TextRange; $tr.Text=$label; $tr.Font.Name="Segoe UI"; $tr.Font.Size=13
  $tr.Font.Italic=-1; $tr.Font.Color.RGB=$GRAY
}

$t=$slide.Shapes.AddTextbox(1,30,12,900,36)
$tr=$t.TextFrame.TextRange; $tr.Text="Data Model (ER)"; $tr.Font.Name="Segoe UI Semibold"; $tr.Font.Size=24; $tr.Font.Bold=-1; $tr.Font.Color.RGB=$NAVY

$X=340
Entity $X 64  "TENANT" $NAVY
Entity $X 132 "PROCESS_DEFINITION" $NAVY
Entity $X 200 "PROCESS_CASE" $NAVY
Entity $X 268 "EVENT_LOG" $NAVY
Entity $X 336 "FINDING" $NAVY
Entity $X 404 "ROOT_CAUSE_REPORT" $NAVY
Entity $X 472 "REMEDIATION_ACTION" $NAVY
Entity 690 64 "HISTORICAL_INCIDENT" $PURPLE

$M = $X + 145
Rel $M 114 $M 132 "1 : N  has"
Rel $M 182 $M 200 "1 : N  has"
Rel $M 250 $M 268 "1 : N  has"
Rel $M 318 $M 336 "contributes (correlation keys)"
Rel $M 386 $M 404 "grouped into incident"
Rel $M 454 $M 472 "1 : N  recommends"
Rel ($X+290) 89 690 89 "1 : N  precedent"

try { if (Test-Path $PngPath) { Remove-Item $PngPath -Force } } catch {}
$slide.Export($PngPath, "PNG", 1920, 1080)
$pres.Close(); $pp.Quit()
Write-Output "PNG: $PngPath"
