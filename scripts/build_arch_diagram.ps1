# POINT: draws a professional architecture diagram in PowerPoint (COM) and
# exports a high-res PNG + an editable PPTX. No downloads.
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_arch_diagram.ps1
param(
  [string]$PngPath  = "$PSScriptRoot\..\docs\architecture.png",
  [string]$PptxPath = "$PSScriptRoot\..\docs\architecture_diagram.pptx"
)
$ErrorActionPreference = "Stop"
$PngPath  = [System.IO.Path]::GetFullPath($PngPath)
$PptxPath = [System.IO.Path]::GetFullPath($PptxPath)

function RGBv($r,$g,$b){ return [int]($r + $g*256 + $b*65536) }
$NAVY=RGBv 31 78 121; $BLUE=RGBv 28 102 201; $GREEN=RGBv 31 157 87
$ORANGE=RGBv 230 126 34; $PINK=RGBv 214 51 108; $PURPLE=RGBv 124 58 237
$WHITE=RGBv 255 255 255; $GRAY=RGBv 120 130 140; $DARK=RGBv 33 37 41

$pp = New-Object -ComObject PowerPoint.Application
$pp.Visible = -1
$pres = $pp.Presentations.Add()
try { $pres.PageSetup.SlideSize = 17 } catch {}   # 17 = Widescreen (960 x 540 pt) - matches the coords below
$slide = $pres.Slides.Add(1, 12)                  # blank
try { $slide.Background.Fill.Solid(); $slide.Background.Fill.ForeColor.RGB = $WHITE } catch {}

function Box($x,$y,$w,$h,$fill,$text,$titleSize=14){
  $s = $slide.Shapes.AddShape(5,$x,$y,$w,$h)      # 5 = rounded rectangle
  $s.Fill.Solid(); $s.Fill.ForeColor.RGB = $fill
  $s.Line.Visible = 0
  $s.Shadow.Visible = -1
  $tf = $s.TextFrame; $tf.VerticalAnchor = 3; $tf.WordWrap = -1
  $tr = $tf.TextRange; $tr.Text = $text
  $tr.Font.Name = "Segoe UI"; $tr.Font.Size = 10; $tr.Font.Color.RGB = $WHITE
  $tr.ParagraphFormat.Alignment = 2
  try { $tr.Paragraphs(1).Font.Size = $titleSize; $tr.Paragraphs(1).Font.Bold = -1 } catch {}
  return $s
}
function Chip($x,$y,$w,$h,$fill,$text){
  $s = $slide.Shapes.AddShape(5,$x,$y,$w,$h)
  $s.Fill.Solid(); $s.Fill.ForeColor.RGB = $fill; $s.Line.Visible = 0
  $tf=$s.TextFrame; $tf.VerticalAnchor=3; $tf.WordWrap=-1
  $tr=$tf.TextRange; $tr.Text=$text; $tr.Font.Name="Segoe UI"; $tr.Font.Size=11
  $tr.Font.Color.RGB=$WHITE; $tr.Font.Bold=-1; $tr.ParagraphFormat.Alignment=2
  return $s
}
function Arrow($x1,$y1,$x2,$y2,$color,$dashed=$false){
  $ln = $slide.Shapes.AddLine($x1,$y1,$x2,$y2)
  $ln.Line.ForeColor.RGB = $color; $ln.Line.Weight = 2.0
  $ln.Line.EndArrowheadStyle = 4                  # stealth arrowhead
  if ($dashed) { $ln.Line.DashStyle = 4 }
  return $ln
}
function Lbl($x,$y,$w,$text,$color){
  $t = $slide.Shapes.AddTextbox(1,$x,$y,$w,16)
  $tr=$t.TextFrame.TextRange; $tr.Text=$text; $tr.Font.Name="Segoe UI"
  $tr.Font.Size=9; $tr.Font.Italic=-1; $tr.Font.Color.RGB=$color; $tr.ParagraphFormat.Alignment=2
  return $t
}

# Title
$t = $slide.Shapes.AddTextbox(1, 30, 16, 900, 38)
$tr=$t.TextFrame.TextRange; $tr.Text="Process Mining - Multi-Source RCA   |   Architecture"
$tr.Font.Name="Segoe UI Semibold"; $tr.Font.Size=22; $tr.Font.Bold=-1; $tr.Font.Color.RGB=$NAVY

# SOURCES (left column)
Lbl 30 66 170 "TELEMETRY SOURCES" $GRAY | Out-Null
Chip 30 90  170 30 $BLUE "Appian  (x3)" | Out-Null
Chip 30 126 170 30 $BLUE "Mule" | Out-Null
Chip 30 162 170 30 $BLUE "Prometheus" | Out-Null
Chip 30 198 170 30 $BLUE "Fluentd" | Out-Null

# TIERS (pipeline)
Box 250 100 200 150 $GREEN  "TIER 1`r`nIngest & Detect`r`n`r`nReaders -> Analyzers`r`nevent_log -> finding" 15 | Out-Null
Box 480 100 200 150 $ORANGE "TIER 2`r`nCorrelate & Diagnose`r`n`r`nGrouper -> Incident`r`nLLM RCA`r`nroot_cause_report" 15 | Out-Null
Box 710 100 200 150 $PINK   "TIER 3`r`nRemediate (human-gated)`r`n`r`nLangGraph:`r`nvalidate-execute-verify" 15 | Out-Null

# EXTERNAL / stores (bottom)
Lbl 30 372 900 "EXTERNAL SYSTEMS & STORES" $GRAY | Out-Null
Chip 30  396 200 44 $PURPLE "historical_incident`r`n(precedent memory)" | Out-Null
Chip 250 396 200 44 $PURPLE "Azure OpenAI`r`n(diagnosis)" | Out-Null
Chip 480 396 200 44 $PURPLE "ServiceNow`r`n(tickets)" | Out-Null
Chip 710 396 200 44 $PURPLE "Streamlit UI`r`n(operator)" | Out-Null

# Main pipeline arrows
Arrow 200 175 250 175 $NAVY | Out-Null
Arrow 450 175 480 175 $NAVY | Out-Null
Arrow 680 175 710 175 $NAVY | Out-Null

# Feedback loops (dashed, labeled)
Arrow 130 396 515 250 $PURPLE $true | Out-Null; Lbl 200 318 170 "precedent >= 0.60" $PURPLE | Out-Null
Arrow 350 396 560 250 $PURPLE $true | Out-Null; Lbl 410 344 130 "LLM (diagnose)" $PURPLE | Out-Null
Arrow 610 250 585 396 $PURPLE $true | Out-Null; Lbl 600 318 130 "file ticket" $PURPLE | Out-Null
Arrow 800 396 800 250 $PURPLE $true | Out-Null; Lbl 745 318 120 "approve" $PURPLE | Out-Null
# 'Learn' loop: resolved tickets sync back into precedent memory - routed BELOW
# the chips so it never crosses a box.
Arrow 560 448 145 470 $GRAY $true | Out-Null; Lbl 250 474 260 "nightly sync of resolved -> memory" $GRAY | Out-Null

# Export the PNG first (the primary output) - robust to locked files.
try { if (Test-Path $PngPath) { Remove-Item $PngPath -Force } } catch {}
$slide.Export($PngPath, "PNG", 1920, 1080)
Write-Output "PNG : $PngPath"
# Editable PPTX is best-effort - skipped if it's currently open in PowerPoint.
try {
  if (Test-Path $PptxPath) { Remove-Item $PptxPath -Force }
  $pres.SaveAs($PptxPath, 24)
  Write-Output "PPTX: $PptxPath"
} catch { Write-Output "note: $PptxPath is open/locked - skipped (PNG still updated). Close it to refresh." }
$pres.Close(); $pp.Quit()
