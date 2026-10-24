# OCR an image with the Windows built-in engine (WinRT Windows.Media.Ocr).
# No third-party dependency. Useful when the current model cannot view images.
#
# Usage:  powershell -File 读图文字.ps1 <image-path>
#
# PITFALLS (all hit in practice, 2026-10-01):
#   1. Keep this file PURE ASCII. PowerShell 5.1 reads a BOM-less .ps1 using the
#      system ANSI codepage (GBK on zh-CN) -> Chinese comments become mojibake
#      and the script fails with bogus parser errors.
#   2. Do NOT use `param($Path)` -- it can arrive empty under some invocations.
#      Positional $args[0] is reliable.
#   3. Do not read the file via StorageFile.GetFileFromPathAsync: it fails for
#      non-ASCII paths and under PS 5.1. Use [IO.File]::ReadAllBytes + the
#      AsRandomAccessStream extension instead (done below).
$Path = $args[0]
if ([string]::IsNullOrWhiteSpace($Path)) { Write-Output '[ERR] usage: <script.ps1> <image-path>'; exit 2 }
if (-not (Test-Path -LiteralPath $Path)) { Write-Output '[ERR] file not found'; exit 2 }

Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
                   $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

$bytes = [System.IO.File]::ReadAllBytes($Path)
$ms    = New-Object System.IO.MemoryStream(, $bytes)
$ras   = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($ms)

$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap  = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])

$engine  = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) {
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage([Windows.Globalization.Language]::new('zh-Hans-CN'))
}
if (-not $engine) { Write-Output '[OCR] engine create failed'; exit 1 }

Write-Output ('[IMG] ' + $decoder.PixelWidth + 'x' + $decoder.PixelHeight + '  [LANG] ' + $engine.RecognizerLanguage.LanguageTag)
$result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
Write-Output ('[LINES] ' + $result.Lines.Count)
foreach ($line in $result.Lines) { Write-Output $line.Text }
