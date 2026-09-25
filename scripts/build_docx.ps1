# POINT: general Markdown -> .docx converter via Word COM (offline; no Pandoc).
# Handles headings, paragraphs, bullet/numbered lists, tables, and code/Mermaid
# blocks (rendered as monospace; Word cannot draw Mermaid). Inline **bold** /
# `code` / [links](url) markers are stripped to clean text.
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_docx.ps1 -InPath serverops\docs\LOW_LEVEL_DESIGN.md
param(
  [Parameter(Mandatory=$true)][string]$InPath,
  [string]$OutPath
)
$ErrorActionPreference = "Stop"
$InPath = [System.IO.Path]::GetFullPath($InPath)
if (-not $OutPath) { $OutPath = [System.IO.Path]::ChangeExtension($InPath, ".docx") }
$OutPath = [System.IO.Path]::GetFullPath($OutPath)

# POINT: read as UTF-8 so em-dashes / arrows / unicode don't mojibake (PS 5.1
# defaults to ANSI, which garbles multi-byte UTF-8 like — into "â€").
$lines = Get-Content -LiteralPath $InPath -Encoding UTF8

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$doc = $word.Documents.Add()
$sel = $word.Selection

function CleanInline([string]$s) {
  $s = $s -replace '\[([^\]]+)\]\([^\)]+\)', '$1'   # [text](url) -> text
  $s = $s -replace '\*\*', ''                       # bold markers
  $s = $s -replace '`', ''                          # inline code ticks
  return $s.Trim()
}
function Emit([string]$style, [string]$text) {
  try { $sel.Style = $doc.Styles.Item($style) } catch {}
  $sel.TypeText((CleanInline $text)); $sel.TypeParagraph()
}
function ParseRow([string]$row) {
  $r = $row.Trim() -replace '^\|','' -replace '\|$',''
  return @($r -split '\|' | ForEach-Object { (CleanInline $_) })
}

# Mermaid blocks are embedded as images IF a pre-rendered PNG exists next to the
# .md named "<docbase>_diagram_<n>.png" (n = order of the mermaid block).
$docBase = [System.IO.Path]::GetFileNameWithoutExtension($InPath)
$docDir  = [System.IO.Path]::GetDirectoryName($InPath)
$mermaidIdx = 0
$i = 0
while ($i -lt $lines.Count) {
  $line = $lines[$i]

  # --- code / mermaid fence ---
  if ($line -match '^\s*```') {
    $lang = ($line -replace '^\s*```','').Trim()
    $code = @(); $i++
    while ($i -lt $lines.Count -and $lines[$i] -notmatch '^\s*```') { $code += $lines[$i]; $i++ }
    $i++
    if ($lang -eq 'mermaid') {
      $mermaidIdx++
      $img = Join-Path $docDir ("{0}_diagram_{1}.png" -f $docBase, $mermaidIdx)
      if (Test-Path $img) {
        # Put the diagram on its own LANDSCAPE page so it embeds wide & legible.
        $sel.InsertBreak(2)                 # wdSectionBreakNextPage
        $sel.PageSetup.Orientation = 1      # wdOrientLandscape
        $usable = $sel.PageSetup.PageWidth - $sel.PageSetup.LeftMargin - $sel.PageSetup.RightMargin
        $sh = $sel.InlineShapes.AddPicture($img, $false, $true)
        $sh.LockAspectRatio = -1; $sh.Width = $usable
        $sel.TypeParagraph()
        Emit "Caption" ("Figure {0}" -f $mermaidIdx)
        $sel.InsertBreak(2)                 # next section
        $sel.PageSetup.Orientation = 0      # back to portrait
        continue
      }
    }
    if ($lang) { Emit "Caption" "[$lang diagram/code]" }
    try { $sel.Style = $doc.Styles.Item("Normal") } catch {}
    $sel.Font.Name = "Consolas"; $sel.Font.Size = 9
    foreach ($c in $code) { $sel.TypeText($c); $sel.TypeText([char]11) }
    $sel.TypeParagraph()
    $sel.Font.Name = "Calibri"; $sel.Font.Size = 11
    continue
  }

  # --- table (header row followed by |---| separator) ---
  if ($line -match '^\s*\|' -and ($i+1) -lt $lines.Count -and $lines[$i+1] -match '^\s*\|[\s:\-\|]+\|\s*$') {
    $rowsData = @(); $rowsData += ,(ParseRow $line); $i += 2
    while ($i -lt $lines.Count -and $lines[$i] -match '^\s*\|') { $rowsData += ,(ParseRow $lines[$i]); $i++ }
    $nR = $rowsData.Count; $nC = @($rowsData[0]).Count
    $sel.EndKey(6) | Out-Null
    $t = $doc.Tables.Add($sel.Range, $nR, $nC)
    $t.Borders.Enable = $true
    try { $t.Style = "Grid Table 4 Accent 1" } catch {}
    for ($r=0; $r -lt $nR; $r++) {
      $cells = @($rowsData[$r])
      for ($c=0; $c -lt $nC; $c++) {
        $val = if ($c -lt $cells.Count) { $cells[$c] } else { "" }
        $t.Cell($r+1, $c+1).Range.Text = $val
      }
    }
    $sel.EndKey(6) | Out-Null
    $sel.TypeParagraph()
    continue
  }

  if ($line -match '^#\s+')   { Emit "Heading 1" ($line -replace '^#\s+','');   $i++; continue }
  if ($line -match '^##\s+')  { Emit "Heading 2" ($line -replace '^##\s+','');  $i++; continue }
  if ($line -match '^###\s+') { Emit "Heading 3" ($line -replace '^###\s+',''); $i++; continue }
  if ($line -match '^\s*---\s*$') { $i++; continue }
  if ($line -match '^\s*>\s?') { Emit "Quote" ($line -replace '^\s*>\s?',''); $i++; continue }
  if ($line -match '^\s*[-*]\s+') { Emit "List Bullet" ($line -replace '^\s*[-*]\s+',''); $i++; continue }
  if ($line -match '^\s*\d+\.\s+') { Emit "List Number" ($line -replace '^\s*\d+\.\s+',''); $i++; continue }
  if ($line -match '^\s*$') { $i++; continue }
  Emit "Normal" $line
  $i++
}

if (Test-Path $OutPath) { Remove-Item $OutPath -Force }
$doc.SaveAs2($OutPath, 16)
$doc.Close()
$word.Quit()
Write-Output "Created: $OutPath"
