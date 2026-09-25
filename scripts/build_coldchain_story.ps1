# POINT: render the "Cold Chain Guardian" IoT storytelling demo deck (PPTX + PNG) via PowerPoint COM.
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_coldchain_story.ps1
param([string]$DocsDir = "$PSScriptRoot\..\docs")
$ErrorActionPreference = "Stop"
$DocsDir = [System.IO.Path]::GetFullPath($DocsDir)

function RGBv($r,$g,$b){ return [int]($r + $g*256 + $b*65536) }
$NAVY=RGBv 27 79 114; $BLUE=RGBv 46 134 193; $ICE=RGBv 174 214 241; $LICE=RGBv 234 244 251
$GREEN=RGBv 30 132 73; $AMBER=RGBv 214 137 16; $RED=RGBv 192 57 43
$WHITE=RGBv 255 255 255; $GRAY=RGBv 120 130 140; $DARK=RGBv 33 37 41

$pp = New-Object -ComObject PowerPoint.Application
$pp.Visible=-1
$W=960; $H=540
$pres=$pp.Presentations.Add(); $pres.PageSetup.SlideWidth=$W; $pres.PageSetup.SlideHeight=$H

function Add-Slide(){ $i=$pp.ActivePresentation.Slides.Count+1; $sl=$pres.Slides.Add($i,12); try{$sl.Background.Fill.Solid();$sl.Background.Fill.ForeColor.RGB=$WHITE}catch{}; return $sl }
function TB($sl,$x,$y,$w,$h,$text,$fs,$color,$bold,$align,$italic){
  $t=$sl.Shapes.AddTextbox(1,$x,$y,$w,$h); $tf=$t.TextFrame; $tf.WordWrap=-1; $tr=$tf.TextRange
  $tr.Text=$text; $tr.Font.Name="Segoe UI"; $tr.Font.Size=[single]$fs; $tr.Font.Color.RGB=[int]$color
  if($bold){$tr.Font.Bold=-1}; if($italic){$tr.Font.Italic=-1}; $tr.ParagraphFormat.Alignment=$align
}
function Header($sl,$title){ $b=$sl.Shapes.AddShape(1,0,0,$W,50); $b.Fill.Solid(); $b.Fill.ForeColor.RGB=$NAVY; $b.Line.Visible=0; $b.Shadow.Visible=0; TB $sl 24 9 ($W-48) 30 $title 19 $WHITE $true 1 $false }
function Box($sl,$x,$y,$w,$h,$fill,$title,$body,$tcolor,$bcolor){
  $s=$sl.Shapes.AddShape(5,$x,$y,$w,$h); $s.Fill.Solid(); $s.Fill.ForeColor.RGB=$fill; $s.Line.Visible=0; $s.Shadow.Visible=-1
  TB $sl ($x+12) ($y+8) ($w-24) 30 $title 13 $tcolor $true 1 $false
  if($body){ TB $sl ($x+12) ($y+38) ($w-24) ($h-46) $body 10.5 $bcolor $false 1 $false }
}
function Arrow($sl,$x1,$y1,$x2,$y2,$color){ $l=$sl.Shapes.AddLine($x1,$y1,$x2,$y2); $l.Line.ForeColor.RGB=$color; $l.Line.Weight=[single]2.5; $l.Line.EndArrowheadStyle=2 }

# ===== SLIDE 1 : HOOK =====
$sl=Add-Slide
$b=$sl.Shapes.AddShape(1,0,140,$W,180); $b.Fill.Solid(); $b.Fill.ForeColor.RGB=$NAVY; $b.Line.Visible=0; $b.Shadow.Visible=0
TB $sl 40 160 ($W-80) 44 "Cold Chain Guardian" 32 $WHITE $true 1 $false
TB $sl 40 214 ($W-80) 30 "When the freezer fails at 65 mph - an IoT + agentic response, against the clock" 15 $ICE $false 1 $true
TB $sl 40 268 ($W-80) 30 "A truckload of ice cream.  Cincinnati -> Columbus.  90 minutes to save it." 14 $WHITE $true 1 $false
TB $sl 40 360 ($W-80) 24 "Every minute the cargo warms. This is how the system races the clock - and keeps the business in the loop." 12 $GRAY $false 1 $true

# ===== SLIDE 2 : THE SETUP / STAKES =====
$sl=Add-Slide
Header $sl "The setup - what is on the truck, and what is at risk"
Box $sl 40 72 430 200 $BLUE "The run" ("A refrigerated (reefer) truck leaves the Cincinnati DC`nfor a Target store in Columbus - ~100 miles, ~2 hours.`n`nCargo: a full load of ICE CREAM.`nMust stay frozen at -18 C or below the whole way.") $WHITE $WHITE
Box $sl 490 72 430 200 $RED "What is at risk" ("Ice cream is brutally sensitive: even a short warm-up`ncauses melt -> refreeze -> ice crystals -> grainy product.`n`n-> The load can be REJECTED / DESTROYED`n-> Store stockout + missed delivery`n-> Brand + food-safety risk`n-> Full-truck financial loss") $WHITE $WHITE
TB $sl 40 292 ($W-80) 24 "Context: cold-chain failures drive ~$35B in US food waste every year. One warm truck is real money - and a real clock." 12 $NAVY $true 1 $true

# ===== SLIDE 3 : THE INCIDENT + CLOCK =====
$sl=Add-Slide
Header $sl "The incident - the clock starts now"
TB $sl 40 66 ($W-80) 26 "Mid-route, an IoT temperature sensor on the reefer detects the cargo warming past its safe threshold." 14 $DARK $true 1 $false
# countdown bar
$g=$sl.Shapes.AddShape(1,40,150,540,54); $g.Fill.Solid(); $g.Fill.ForeColor.RGB=$GREEN; $g.Line.Visible=0; $g.Shadow.Visible=0
TB $sl 40 165 540 24 "FIX WINDOW  -  0 to 60 minutes" 13 $WHITE $true 1 $false
$r=$sl.Shapes.AddShape(1,580,150,300,54); $r.Fill.Solid(); $r.Fill.ForeColor.RGB=$RED; $r.Line.Visible=0; $r.Shadow.Visible=0
TB $sl 580 165 300 24 "LOSS ZONE  -  60 to 90 min" 13 $WHITE $true 1 $false
TB $sl 24 210 90 20 "0 min" 11 $DARK $true 1 $false
TB $sl 540 210 90 20 "60 min" 11 $GREEN $true 1 $false
TB $sl 835 210 90 20 "90 min" 11 $RED $true 1 $false
TB $sl 40 250 ($W-80) 26 "90 minutes total tolerance before the load is lost.  Target: resolve within 60 minutes.  If not - replace + escalate." 13 $DARK $true 1 $true
TB $sl 40 320 ($W-80) 24 "The tension is real: the obstacle is a genuinely perishable, high-value load with a hard deadline." 11 $GRAY $false 1 $true

# ===== SLIDE 4 : AGENTIC RESPONSE =====
$sl=Add-Slide
Header $sl "The agentic response - inform, accept, reach, close"
$stepW=205; $stepH=104; $y=80
$xs=@(40,255,470,685)
Box $sl $xs[0] $y $stepW $stepH $BLUE "1. Inform Tech" "Alert auto-dispatched to the nearest available technician" $WHITE $WHITE
Box $sl $xs[1] $y $stepW $stepH $BLUE "2. Tech Accepts" "Technician acknowledges within SLA" $WHITE $WHITE
Box $sl $xs[2] $y $stepW $stepH $BLUE "3. Tech Reaches" "Dispatched and on-site at the reefer" $WHITE $WHITE
Box $sl $xs[3] $y $stepW $stepH $GREEN "4. Issue Closed" "Cooling restored; temp back in range and verified" $WHITE $WHITE
for($i=0;$i -lt 3;$i++){ Arrow $sl ($xs[$i]+$stepW) ($y+$stepH/2) ($xs[$i+1]) ($y+$stepH/2) $GRAY }
# escalation banner
$e=$sl.Shapes.AddShape(5,40,230,880,70); $e.Fill.Solid(); $e.Fill.ForeColor.RGB=$RED; $e.Line.Visible=0; $e.Shadow.Visible=-1
TB $sl 54 240 852 26 "If NOT closed within the 60-minute window:" 14 $WHITE $true 1 $false
TB $sl 54 266 852 24 "Escalate to Senior Manager   -   Issue a Replacement load   -   Mark Loss (business impact recorded)" 12 $WHITE $false 1 $false
TB $sl 40 320 ($W-80) 24 "Every step is SLA-timed. Nothing stalls silently - the clock drives the next action automatically." 11 $GRAY $false 1 $true
TB $sl 40 360 ($W-80) 40 "IoT sensors track the reefer in real time; agents orchestrate inform -> accept -> reach -> close, and enforce the deadline." 11 $DARK $false 1 $true

# ===== SLIDE 5 : TWO OUTCOMES =====
$sl=Add-Slide
Header $sl "Two outcomes - but never a silent failure"
Box $sl 40 80 430 240 $GREEN "Outcome A - Fixed in time (<= 60 min)" ("Cooling restored inside the window.`n`n-> Cargo SAVED`n-> Store delivered on time`n-> Zero loss`n-> Full temperature + action audit trail retained") $WHITE $WHITE
Box $sl 490 80 430 240 $AMBER "Outcome B - Not fixed in time" ("The system does not just fail - it protects the business:`n`n-> Replacement load dispatched (store still served)`n-> Senior Manager escalation with $ impact`n-> Loss recorded for audit / insurance`n-> Root cause captured for next time") $WHITE $WHITE
TB $sl 40 336 ($W-80) 24 "Either way, the business is informed in real time and the store is protected. No surprise spoilage, no silent loss." 12 $NAVY $true 1 $true

# ===== SLIDE 6 : WHY IT MATTERS =====
$sl=Add-Slide
Header $sl "Why it matters - and where it goes next"
$cw=282; $ch=110; $gx=17; $x0=40; $y0=72
$t=@("Real-time visibility","SLA-driven autonomy","Loss prevention + replacement","Escalation with business impact","Full audit trail","Proven ROI")
$bd=@(
 "IoT senses the reefer continuously - deviations flagged before spoilage.",
 "Agents run inform -> accept -> reach -> close and enforce the deadline automatically.",
 "Fix in time, or dispatch a replacement so the store is never left short.",
 "Senior manager sees the $ impact live - decisions made with the real number.",
 "Temperature log + every action + timings retained for audit and insurance.",
 "Sensors < $50; $5-15k saved per site / year; ROI in 3-6 months.")
$cols=@($BLUE,$BLUE,$GREEN,$AMBER,$NAVY,$GREEN)
for($i=0;$i -lt 6;$i++){ $c=$i%3; $r=[int]([Math]::Floor($i/3)); Box $sl ($x0+$c*($cw+$gx)) ($y0+$r*($ch+16)) $cw $ch $cols[$i] $t[$i] $bd[$i] $WHITE $WHITE }
TB $sl 40 400 ($W-80) 40 "Same agentic pattern as our incident platform: detect -> act (inform/accept/reach) -> escalate/replace -> close -> learn. One playbook, many domains." 12 $NAVY $true 1 $true

# ===== SAVE + EXPORT =====
$pptx=Join-Path $DocsDir "COLD_CHAIN_STORY.pptx"
if(Test-Path $pptx){ Remove-Item $pptx -Force }
$pres.SaveAs($pptx,24)
for($i=1;$i -le 6;$i++){ $png=Join-Path $DocsDir ("COLD_CHAIN_STORY_$i.png"); if(Test-Path $png){Remove-Item $png -Force}; $pres.Slides.Item($i).Export($png,"PNG",1920,1080) }
$pres.Close(); $pp.Quit()
Write-Output "Wrote $pptx (6 slides) + PNG previews"
