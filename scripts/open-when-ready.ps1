<#
.SYNOPSIS
    等界面端口起来之后再打开浏览器。

.DESCRIPTION
    gui.py 启动后要十几秒才 listen,立刻打开浏览器只会看到"无法连接"。
    这个脚本被 launch.ps1 以后台进程方式拉起,轮询到 200 就打开默认浏览器。
    超时就静默退出 —— 它不是关键路径,失败也不该影响启动。
#>
[CmdletBinding()]
param(
    [string] $Url = 'http://127.0.0.1:7860',
    [int]    $TimeoutSeconds = 180
)

for ($i = 0; $i -lt $TimeoutSeconds; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { Start-Process $Url; exit 0 }
    } catch {
        # 还没起来,继续等
    }
}
exit 1
