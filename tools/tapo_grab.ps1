# Grab one frame from a Tapo (RTSP, H.264) camera on the CoralBoard and save it as ..\snapshots\tapo_view.jpg.
# The board decodes in software (avdec_h264; no hardware H.264 decoder in its GStreamer, 2026-10-30).
# Camera address: run once with -SetCamera. It asks for the camera's IP and Tapo "camera account" (password hidden)
# and saves the RTSP base URL on the board in /home/root/.printwatch/camera_url (root-only, mode 600). Later runs read it
# there, so they only ask for the board's root password. -Stream 2 picks the 360p stream (default 1 = 1080p).
# The account is percent-encoded (everything but letters and digits), so special characters survive ssh, sh and the URL.
# That is encoding, not encryption: anyone who can log in to the board as root can read the camera account.
# The frame comes back as base64 text: PowerShell 5.1's pipeline would corrupt raw JPEG bytes.
# The board has no `base64` command, so Python's base64 module encodes it.
# Run: powershell -ExecutionPolicy Bypass -File .\tools\tapo_grab.ps1   (printer address from $env:PRINTWATCH_HOST or -Printer)
# -Detect also runs the stock YOLOv8n (COCO) demo on the frame on the NPU and brings back the annotated
# image (..\snapshots\tapo_detect.jpg). Everything is written to /dev/shm (RAM), not the SD card.
# -Threshold sets the minimum confidence to report (the demo's CONF_THRESH is 0.25); lower it to see weak guesses.
# It runs a copy of the demo in /dev/shm with the value patched in, so the installed demo stays unchanged.
param([string]$Printer = $env:PRINTWATCH_HOST, [switch]$SetCamera, [string]$Stream = '1', [switch]$Detect, [double]$Threshold = 0.25)

if (-not $Printer) { Write-Host 'Set PRINTWATCH_HOST or pass -Printer <printer address>'; exit 1 }

function Encode-UrlPart([string]$s) {
    -join ([Text.Encoding]::UTF8.GetBytes($s) | ForEach-Object {
        if (($_ -ge 48 -and $_ -le 57) -or ($_ -ge 65 -and $_ -le 90) -or ($_ -ge 97 -and $_ -le 122)) { [char]$_ }
        else { '%{0:X2}' -f $_ }
    })
}

$CamFile = '/home/root/.printwatch/camera_url'

if ($SetCamera) {
    $ip = Read-Host 'Tapo camera IP'
    $user = Read-Host 'Camera account user'
    $secure = Read-Host 'Camera account password' -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    $pass = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
    $base = 'rtsp://' + (Encode-UrlPart $user) + ':' + (Encode-UrlPart $pass) + "@${ip}:554"
    $pass = $null
    # The encoded URL holds only letters, digits and % : @ / . so it needs no quoting on the board.
    ssh -p 2222 "root@$Printer" ('umask 077; mkdir -p /home/root/.printwatch && echo {0} > {1} && echo Camera saved on the board in {1}' -f $base, $CamFile)
    $base = $null
    exit
}

# Read the saved base URL on the board; the camera account never comes back to the PC.
$url = '$(cat ' + $CamFile + ')/stream' + $Stream

# The board has neither `timeout` nor `pkill`: run gst in the background and kill it by PID after 10 s.
# First stop any gst-launch left running by an interrupted earlier grab.
$remote = 'test -s ' + $CamFile + ' || { echo No camera saved on the board: run tapo_grab.ps1 -SetCamera first 1>&2; exit 1; }; '
$remote += 'kill $(pidof gst-launch-1.0) 2>/dev/null; gst-launch-1.0 -q rtspsrc location={0} protocols=tcp ! rtph264depay ! h264parse ! avdec_h264 ! videorate drop-only=true max-rate=1 ! videoconvert ! jpegenc ! multifilesink location=/dev/shm/tapo.jpg 1>&2 & G=$!; sleep 10; kill $G; F=/dev/shm/tapo.jpg; ' -f $url
if ($Detect) {
    # run_cli.py writes output_yolo.jpg to the current directory, and only when something was detected.
    $thr = $Threshold.ToString([Globalization.CultureInfo]::InvariantCulture)
    $remote += ('E=/home/root/sl2610-examples; rm -rf /dev/shm/sx; mkdir -p /dev/shm/sx && ln -s $E/app_utils /dev/shm/sx/app_utils && cp -r $E/object_detection /dev/shm/sx/od && sed -i ''s/^CONF_THRESH = .*/CONF_THRESH = {0}/'' /dev/shm/sx/od/vision.py; grep ^CONF_THRESH /dev/shm/sx/od/vision.py 1>&2; ' -f $thr)
    $remote += 'cd /dev/shm && $E/.venv/bin/python3 /dev/shm/sx/od/run_cli.py --image /dev/shm/tapo.jpg --model $E/models/Synaptics/yolov8-od-nano-320-int8-torq/yolo_od.vmfb --labels /dev/shm/sx/od/labels.json --save-image 1>&2; [ -f /dev/shm/output_yolo.jpg ] && F=/dev/shm/output_yolo.jpg; rm -rf /dev/shm/sx; '
}
$remote += 'python3 -m base64 $F; rm -f /dev/shm/tapo.jpg /dev/shm/output_yolo.jpg'
$b64 = ssh -p 2222 "root@$Printer" $remote
$url = $null; $remote = $null

$out = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot $(if ($Detect) { '..\snapshots\tapo_detect.jpg' } else { '..\snapshots\tapo_view.jpg' })))
$text = ($b64 -join '')
if (-not $text) { Write-Host 'No frame came back (see the errors above).'; exit 1 }
[IO.File]::WriteAllBytes($out, [Convert]::FromBase64String($text))
Write-Host "Saved $out ($((Get-Item $out).Length) bytes)"
