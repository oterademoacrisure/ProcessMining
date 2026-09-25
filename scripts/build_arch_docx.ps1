# POINT: build architecture.docx via Word COM (offline; no Pandoc).
# Embeds the rendered diagram PNG (Word can't render Mermaid) + headings/text.
#   powershell -ExecutionPolicy Bypass -File serverops\scripts\build_arch_docx.ps1
param(
  [string]$OutPath = "$PSScriptRoot\..\docs\architecture.docx",
  [string]$Png     = "$PSScriptRoot\..\docs\architecture.png"
)
$ErrorActionPreference = "Stop"
$OutPath = [System.IO.Path]::GetFullPath($OutPath)
$Png     = [System.IO.Path]::GetFullPath($Png)

$word = New-Object -ComObject Word.Application
$word.Visible = $false
$doc = $word.Documents.Add()
$sel = $word.Selection

function Para($style, $text) { $sel.Style = $doc.Styles.Item($style); $sel.TypeText($text); $sel.TypeParagraph() }

Para "Title"     "Process Mining - Multi-Source RCA"
Para "Subtitle"  "Architecture"
Para "Normal"    "Engineering architecture of the multi-source root-cause analysis platform. Logs from every source are correlated into one incident, diagnosed across layers, and remediated - with ServiceNow precedent feeding the diagnosis and a human approving the fix."

Para "Heading 1" "1. Platform Architecture"
if (Test-Path $Png) {
  $shape = $sel.InlineShapes.AddPicture($Png, $false, $true)
  $shape.LockAspectRatio = -1
  $shape.Width = 468           # ~6.5in usable page width
  $sel.TypeParagraph()
} else {
  Para "Normal" "[architecture.png not found - run build_arch_diagram.ps1 first]"
}
Para "Caption"   "Figure 1. Sources -> Tier 1 (Ingest/Detect) -> Tier 2 (Correlate/Diagnose) -> Tier 3 (Remediate). ServiceNow precedent feeds Tier 2; operators approve via the Streamlit UI; resolved tickets sync back into precedent memory."

Para "Heading 1" "2. End-to-End Flow (per incident)"
Para "Normal"    "Logs (all sources) -> event_log -> finding -> Incident (grouped across sources) -> Diagnose (LLM + precedent) -> root_cause_report -> ServiceNow ticket + recommended actions -> (human approves) -> LangGraph remediation (validate -> execute -> verify)."
Para "Normal"    "The database tables are the hand-off points between stages; each stage polls unprocessed rows (watermarked by processed_at) and writes the next table."

Para "Heading 1" "3. Runner Loop (production = always-on)"
Para "Normal"    "runner.py runs continuously (~every 30s), executing four cycles in order:"
Para "List Paragraph" "1. INGEST  - read sources, persist to event_log"
Para "List Paragraph" "2. DISPATCH - analyzers produce findings"
Para "List Paragraph" "3. INVESTIGATE - group into incidents, LLM diagnoses, write root_cause_report"
Para "List Paragraph" "4. REMEDIATE - execute only human-approved actions"

Para "Heading 1" "Tiers & Pillars"
Para "List Paragraph" "Pillar - BPM / integration: Appian (x3), Mule"
Para "List Paragraph" "Pillar - Infrastructure / runtime: Prometheus, Fluentd"
Para "List Paragraph" "External / stores: Azure OpenAI (diagnosis), ServiceNow (tickets + precedent), historical_incident (memory), Streamlit UI (operator)"
Para "Normal"    "Value grows with each pillar: the more layers that agree, the higher the diagnostic confidence."

if (Test-Path $OutPath) { Remove-Item $OutPath -Force }
$doc.SaveAs2($OutPath, 16)    # 16 = wdFormatDocumentDefault (.docx)
$doc.Close()
$word.Quit()
Write-Output "Created: $OutPath"
