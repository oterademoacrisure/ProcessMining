# POINT: render LLD diagrams 2-4 (2 sequence diagrams + 1 state machine) via
# PowerPoint COM -> PNGs named LOW_LEVEL_DESIGN_diagram_{2,3,4}.png.
# Larger fonts so they stay legible when embedded in the docx (landscape page).
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_lld_diagrams.ps1
param([string]$DocsDir = "$PSScriptRoot\..\docs")
$ErrorActionPreference = "Stop"
$DocsDir = [System.IO.Path]::GetFullPath($DocsDir)

function RGBv($r,$g,$b){ return [int]($r + $g*256 + $b*65536) }
$NAVY=RGBv 31 78 121; $GRAY=RGBv 130 140 150; $WHITE=RGBv 255 255 255; $DARK=RGBv 33 37 41
$GREEN=RGBv 31 157 87; $PINK=RGBv 214 51 108; $ORANGE=RGBv 230 126 34

$pp = New-Object -ComObject PowerPoint.Application
$pp.Visible = -1

function New-Canvas($w,$h){
  $pres = $pp.Presentations.Add()
  $pres.PageSetup.SlideWidth = $w; $pres.PageSetup.SlideHeight = $h
  $sl = $pres.Slides.Add(1,12)
  try { $sl.Background.Fill.Solid(); $sl.Background.Fill.ForeColor.RGB = $WHITE } catch {}
  return @($pres,$sl)
}
function Box($sl,$x,$y,$w,$h,$fill,$text,$fs=16){
  $s=$sl.Shapes.AddShape(5,$x,$y,$w,$h); $s.Fill.Solid(); $s.Fill.ForeColor.RGB=$fill
  $s.Line.Visible=0; $s.Shadow.Visible=-1
  $tf=$s.TextFrame; $tf.VerticalAnchor=3; $tf.WordWrap=-1
  $tr=$tf.TextRange; $tr.Text=$text; $tr.Font.Name="Segoe UI"; $tr.Font.Size=$fs
  $tr.Font.Color.RGB=$WHITE; $tr.Font.Bold=-1; $tr.ParagraphFormat.Alignment=2
}
function VLine($sl,$x,$y1,$y2){ $l=$sl.Shapes.AddLine($x,$y1,$x,$y2); $l.Line.ForeColor.RGB=$GRAY; $l.Line.Weight=1; $l.Line.DashStyle=4 }
function Msg($sl,$x1,$x2,$y,$text,$ret){
  $l=$sl.Shapes.AddLine($x1,$y,$x2,$y); $l.Line.ForeColor.RGB=$NAVY; $l.Line.Weight=1.75; $l.Line.EndArrowheadStyle=4
  if ($ret) { $l.Line.DashStyle=4; $l.Line.ForeColor.RGB=$GRAY }
  $lo=[Math]::Min($x1,$x2); $w=[Math]::Abs($x2-$x1)
  $t=$sl.Shapes.AddTextbox(1,$lo,($y-20),[Math]::Max($w,140),18)
  $tr=$t.TextFrame.TextRange; $tr.Text=$text; $tr.Font.Name="Segoe UI"; $tr.Font.Size=12; $tr.Font.Color.RGB=$DARK; $tr.ParagraphFormat.Alignment=2
}
function SelfMsg($sl,$x,$y,$text){
  $t=$sl.Shapes.AddTextbox(1,($x+8),($y-9),320,18)
  $tr=$t.TextFrame.TextRange; $tr.Text=("[self] "+$text); $tr.Font.Name="Segoe UI"; $tr.Font.Size=11; $tr.Font.Italic=-1; $tr.Font.Color.RGB=$GRAY
}
function Title($sl,$text){
  $t=$sl.Shapes.AddTextbox(1,30,12,1100,34)
  $tr=$t.TextFrame.TextRange; $tr.Text=$text; $tr.Font.Name="Segoe UI Semibold"; $tr.Font.Size=22; $tr.Font.Bold=-1; $tr.Font.Color.RGB=$NAVY
}

function Render-Sequence($title,$parts,$msgs,$out){
  $W=1040; $rowH=42; $topY=62; $boxH=48
  $H=$topY+$boxH+34 + ($msgs.Count*$rowH) + 40
  $c=New-Canvas $W $H; $pres=$c[0]; $sl=$c[1]
  Title $sl $title
  $n=$parts.Count; $colW=($W-40)/$n
  $cx=@(); for($k=0;$k -lt $n;$k++){ $cx += [int](20+$colW*$k+$colW/2) }
  $bw=[int]($colW-14)
  $lifeBottom=$topY+$boxH+34+($msgs.Count*$rowH)+10
  for($k=0;$k -lt $n;$k++){
    Box $sl ($cx[$k]-$bw/2) $topY $bw $boxH $NAVY $parts[$k] 15
    VLine $sl $cx[$k] ($topY+$boxH) $lifeBottom
  }
  $y=$topY+$boxH+38
  foreach($m in $msgs){
    if ($m.self){ SelfMsg $sl $cx[$m.f] $y $m.t }
    else { Msg $sl $cx[$m.f] $cx[$m.to] $y $m.t $m.ret }
    $y+=$rowH
  }
  if(Test-Path $out){Remove-Item $out -Force}
  $sl.Export($out,"PNG",[int]($W*2),[int]($H*2))
  $pres.Close()
}

# diagram_2: Tier 1 (ingest + dispatch)
$p2=@("runner","Reader","persist","Dispatcher","Analyzer","Emitter")
$m2=@(
  @{f=0;to=1;t="read()";ret=$false},
  @{f=1;to=0;t="EventLogPayload[]";ret=$true},
  @{f=0;to=2;t="persist(payloads)";ret=$false},
  @{f=2;self=$true;t="resolve natural keys -> case_id"},
  @{f=2;to=0;t="event_log rows";ret=$true},
  @{f=0;to=3;t="run_once()";ret=$false},
  @{f=3;self=$true;t="poll + group by source_type"},
  @{f=3;to=4;t="analyze(rows)";ret=$false},
  @{f=4;to=3;t="Finding[]";ret=$true},
  @{f=3;to=5;t="handle(findings)";ret=$false},
  @{f=5;to=3;t="finding rows";ret=$true},
  @{f=3;self=$true;t="mark_processed"}
)
Render-Sequence "Tier 1 - Ingest & Dispatch" $p2 $m2 (Join-Path $DocsDir "LOW_LEVEL_DESIGN_diagram_2.png")

# diagram_3: Tier 2 (investigate with precedent)
$p3=@("InvDispatch","grouper","Investigator","precedents","Azure LLM","sinks")
$m3=@(
  @{f=0;self=$true;t="poll findings (sev >= high)"},
  @{f=0;to=1;t="group_into_incidents()";ret=$false},
  @{f=1;to=0;t="Incident[]";ret=$true},
  @{f=0;to=2;t="investigate() [loop]";ret=$false},
  @{f=2;self=$true;t="fetch related findings + events"},
  @{f=2;to=3;t="find_precedents(...)";ret=$false},
  @{f=3;to=2;t="Precedent[] (>=0.60)";ret=$true},
  @{f=2;to=4;t="prompt(evidence+precedent)";ret=$false},
  @{f=4;to=2;t="summary + actions";ret=$true},
  @{f=2;to=0;t="RootCauseReport";ret=$true},
  @{f=0;to=5;t="handle(report)";ret=$false},
  @{f=0;self=$true;t="mark findings processed"}
)
Render-Sequence "Tier 2 - Correlate & Diagnose (with precedent)" $p3 $m3 (Join-Path $DocsDir "LOW_LEVEL_DESIGN_diagram_3.png")

# diagram_4: Tier 3 remediation state machine
$out4=Join-Path $DocsDir "LOW_LEVEL_DESIGN_diagram_4.png"
$c=New-Canvas 1000 470; $pres=$c[0]; $sl=$c[1]
Title $sl "Tier 3 - Remediation (LangGraph state machine)"
Box $sl 360 64  240 52 $NAVY   "VALIDATE" 18
Box $sl 360 160 240 52 $NAVY   "EXECUTE" 18
Box $sl 360 256 240 52 $NAVY   "VERIFY" 18
Box $sl 360 352 240 52 $GREEN  "report_verified" 15
Box $sl 710 64  250 52 $PINK   "report_rejected" 15
Box $sl 710 160 250 52 $PINK   "report_failed" 15
Box $sl 710 256 250 52 $ORANGE "report_unverified" 15
function Arr($sl,$x1,$y1,$x2,$y2,$label){
  $l=$sl.Shapes.AddLine($x1,$y1,$x2,$y2); $l.Line.ForeColor.RGB=$NAVY; $l.Line.Weight=1.75; $l.Line.EndArrowheadStyle=4
  $t=$sl.Shapes.AddTextbox(1,([Math]::Min($x1,$x2)+4),(([Math]::Min($y1,$y2)+[Math]::Max($y1,$y2))/2-10),170,18)
  $tr=$t.TextFrame.TextRange; $tr.Text=$label; $tr.Font.Name="Segoe UI"; $tr.Font.Size=12; $tr.Font.Italic=-1; $tr.Font.Color.RGB=$GRAY
}
Arr $sl 480 116 480 160 "allow-listed"
Arr $sl 480 212 480 256 "exit_code == 0"
Arr $sl 480 308 480 352 "passed"
Arr $sl 600 90  710 90  "denied"
Arr $sl 600 186 710 186 "nonzero"
Arr $sl 600 282 710 282 "verify failed"
if(Test-Path $out4){Remove-Item $out4 -Force}
$sl.Export($out4,"PNG",2000,940)
$pres.Close()

$pp.Quit()
Write-Output "Rendered diagrams 2, 3, 4 into $DocsDir"
