$com = New-Object -ComObject SDKCONTPAQNG.TSdkSesion
$com.iniciaConexion()
$com.firmaUsuarioParams('CONTADOR', 'Contador.1')
$com.abreEmpresa('ctCPC_2021')

$cuenta = New-Object -ComObject SDKCONTPAQNG.TSdkCuenta
$cuenta.setSesion($com)

$tipo = $cuenta.GetType()
$miembros = $tipo.GetMembers() | Sort-Object Name -Unique
foreach ($m in $miembros) {
    Write-Host $m.Name
}

$com.cierraEmpresa()
$com.finalizaConexion()
