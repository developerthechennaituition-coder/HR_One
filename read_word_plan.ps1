$p = 'c:\Users\MY PC\Downloads\HRMS_Sprint_1_Today_Task_Plan_F002_F005.docx'
$output = 'f:\HRMS-Project\doc_text.txt'
Write-Output "PATH=$p"
Write-Output "EXISTS=$([System.IO.File]::Exists($p))"
if ([System.IO.File]::Exists($p)) {
    $zip = [System.IO.Compression.ZipFile]::OpenRead($p)
    try {
        $entry = $zip.Entries | Where-Object { $_.FullName -like 'word/document.xml' }
        if ($null -ne $entry) {
            $stream = $entry.Open()
            $reader = New-Object System.IO.StreamReader($stream)
            $xml = $reader.ReadToEnd()
            $reader.Dispose(); $stream.Dispose()
            [xml]$doc = $xml
            $ns = New-Object System.Xml.XmlNamespaceManager($doc.NameTable)
            $ns.AddNamespace('w', 'http://schemas.openxmlformats.org/wordprocessingml/2006/main')
            $nodes = $doc.SelectNodes('//w:t', $ns)
            $text = ($nodes | ForEach-Object { $_.InnerText }) -join "`n"
            [System.IO.File]::WriteAllText($output, $text.Substring(0, [Math]::Min(20000, $text.Length)), [System.Text.Encoding]::UTF8)
            Write-Output "WROTE=$output"
            Write-Output $text.Substring(0, 2000)
        }
        else {
            Write-Output 'NO document.xml' 
        }
    }
    finally {
        $zip.Dispose()
    }
}
