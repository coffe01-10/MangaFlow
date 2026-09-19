$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Net.Http
$bin = "C:\Windows\Temp\nui67bin"
$null = [Reflection.Assembly]::LoadFrom((Join-Path $bin "System.Text.Json.dll"))
$null = [Reflection.Assembly]::LoadFrom((Join-Path $bin "System.Text.Encodings.Web.dll"))
$null = [Reflection.Assembly]::LoadFrom((Join-Path $bin "System.Text.Encodings.Web.dll"))
$null = [Reflection.Assembly]::LoadFrom((Join-Path $bin "System.Memory.dll"))
$null = [Reflection.Assembly]::LoadFrom((Join-Path $bin "System.Buffers.dll"))
$null = [Reflection.Assembly]::LoadFrom((Join-Path $bin "System.Runtime.CompilerServices.Unsafe.dll"))
$null = [Reflection.Assembly]::LoadFrom((Join-Path $bin "System.Threading.Tasks.Extensions.dll"))
$null = [Reflection.Assembly]::LoadFrom((Join-Path $bin "System.ValueTuple.dll"))
$null = [Reflection.Assembly]::LoadFrom((Join-Path $bin "System.Numerics.Vectors.dll"))
$asm = [Reflection.Assembly]::LoadFrom((Join-Path $bin "MangaFlow.Native.dll"))

$client = New-Object System.Net.Http.HttpClient
$providersJson = $client.GetStringAsync("http://127.0.0.1:5078/api/v1/providers").Result
$modelsJson = $client.GetStringAsync("http://127.0.0.1:5078/api/v1/models").Result

$providersDoc = [System.Text.Json.JsonDocument]::Parse($providersJson)
$modelsDoc = [System.Text.Json.JsonDocument]::Parse($modelsJson)

$providers = [System.Linq.Enumerable]::ToList($providersDoc.RootElement.EnumerateArray())
$catalog = [System.Linq.Enumerable]::ToList($modelsDoc.RootElement.EnumerateArray())
Write-Output ("providers: " + $providers.Count + " catalog: " + $catalog.Count)

$flags = $asm.GetType("MangaFlow.Native.JsonFields")
$textM = $flags.GetMethod("Text", [System.Reflection.BindingFlags]"Public,Static")
$arrayM = $flags.GetMethod("Array", [System.Reflection.BindingFlags]"Public,Static")
$flagM = $flags.GetMethod("Flag", [System.Reflection.BindingFlags]"Public,Static")
$numberM = $flags.GetMethod("Number", [System.Reflection.BindingFlags]"Public,Static")

try {
    $configured = 0
    foreach ($p in $providers) {
        $conns = $arrayM.Invoke($null, @($p, "connections"))
        foreach ($c in $conns) {
            $null = $flagM.Invoke($null, @($c, "configured"))
            $null = $numberM.Invoke($null, @($c, "model_count"))
            $null = $numberM.Invoke($null, @($c, "latency_ms"))
            $null = $numberM.Invoke($null, @($c, "key_count"))
            $null = $textM.Invoke($null, @($c, "health_state"))
            $null = $textM.Invoke($null, @($c, "message"))
            $null = $textM.Invoke($null, @($c, "protocol"))
            $null = $textM.Invoke($null, @($c, "base_url"))
            $null = $textM.Invoke($null, @($c, "credential_source"))
        }
        $null = $textM.Invoke($null, @($p, "name"))
        $null = $textM.Invoke($null, @($p, "category"))
        $null = $textM.Invoke($null, @($p, "risk_label"))
        $null = $textM.Invoke($null, @($p, "description"))
    }
    Write-Output ("connection field access OK; configured=" + $configured)
    # models catalog fields used by ModelRow
    foreach ($m in $catalog) {
        foreach ($name in @("model_type","enabled","display_enabled","verified","operations","input_modalities","output_modalities","display_name","provider","model_id","logical_alias","capabilities","accepts_explicit_mask","whole_image_reference_only","max_reference_images","resolutions","preview_resolutions","supports_instruction_region_edit","preserves_outside_region","region_capability_sources","regions","auto_eligible","priority","confidence","source")) {
            $null = $textM.Invoke($null, @($m, $name))
            $null = $numberM.Invoke($null, @($m, $name))
        }
    }
    Write-Output "model catalog field access OK"
} catch {
    Write-Output ("THREW: " + $_.Exception.GetBaseException().Message)
    if ($_.Exception.GetBaseException().StackTrace) { Write-Output $_.Exception.GetBaseException().StackTrace }
}
