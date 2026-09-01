<#
    Turn the Windows playback volume up before the announcer starts.

    A PA machine that somebody once turned down is a PA machine that whispers,
    and nobody thinks to check the Windows volume slider on a computer locked
    in a cupboard. So it is set on every start.

    Loudness should be trimmed at the AMPLIFIER, not in Windows: full digital
    level into a correctly set amplifier is clean, whereas a quiet digital
    signal pushed up by the amplifier brings the noise floor up with it.

    Controlled by PA_SET_SYSTEM_VOLUME in .env:
        100          per cent, the default
        85           any number from 1 to 100
        off / false  leave the Windows volume alone
#>

param(
    [int]$Level = 0
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# ------------------------------------------------------- what level, if any
if ($Level -le 0) {
    $Level = 100
    $envFile = Join-Path $root '.env'
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            $trimmed = $line.Trim()
            if ($trimmed -like 'PA_SET_SYSTEM_VOLUME=*') {
                $value = $trimmed.Substring('PA_SET_SYSTEM_VOLUME='.Length).Trim().Trim('"').Trim("'")
                if ($value -match '^(off|false|no|none)$') {
                    Write-Host "  Volume: leaving the Windows volume alone (PA_SET_SYSTEM_VOLUME=$value)."
                    exit 0
                }
                if ($value -match '^\d+$') { $Level = [int]$value }
            }
        }
    }
}

if ($Level -lt 1) { $Level = 1 }
if ($Level -gt 100) { $Level = 100 }

# ------------------------------------------------- talk to the audio device
# Windows has no command for this, so we call the Core Audio COM interface
# directly. The empty f/g/h/... methods are real methods we do not use; they
# have to be declared so the ones we do use land at the right place in the
# interface. Do not reorder them.
$source = @"
using System;
using System.Runtime.InteropServices;

[Guid("5CDF2C82-841E-4546-9722-0CF74078229A"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
    int RegisterControlChangeNotify(IntPtr p);
    int UnregisterControlChangeNotify(IntPtr p);
    int GetChannelCount(out uint c);
    int SetMasterVolumeLevel(float level, Guid ctx);
    int SetMasterVolumeLevelScalar(float level, Guid ctx);
    int GetMasterVolumeLevel(out float level);
    int GetMasterVolumeLevelScalar(out float level);
    int SetChannelVolumeLevel(uint ch, float level, Guid ctx);
    int SetChannelVolumeLevelScalar(uint ch, float level, Guid ctx);
    int GetChannelVolumeLevel(uint ch, out float level);
    int GetChannelVolumeLevelScalar(uint ch, out float level);
    int SetMute([MarshalAs(UnmanagedType.Bool)] bool mute, Guid ctx);
    int GetMute([MarshalAs(UnmanagedType.Bool)] out bool mute);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    int Activate(ref Guid iid, int clsCtx, IntPtr activationParams,
                 [MarshalAs(UnmanagedType.IUnknown)] out object iface);
}

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int dataFlow, int mask, IntPtr devices);
    int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice endpoint);
}

[ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
class MMDeviceEnumeratorComObject { }

public class PaVolume {
    static IAudioEndpointVolume Endpoint() {
        IMMDeviceEnumerator enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumeratorComObject());
        IMMDevice device;
        Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(0, 1, out device));
        Guid iid = typeof(IAudioEndpointVolume).GUID;
        object raw;
        Marshal.ThrowExceptionForHR(device.Activate(ref iid, 23, IntPtr.Zero, out raw));
        return (IAudioEndpointVolume)raw;
    }

    public static float Get() {
        float level;
        Marshal.ThrowExceptionForHR(Endpoint().GetMasterVolumeLevelScalar(out level));
        return level;
    }

    public static void Set(float level) {
        IAudioEndpointVolume endpoint = Endpoint();
        Marshal.ThrowExceptionForHR(endpoint.SetMasterVolumeLevelScalar(level, Guid.Empty));
        Marshal.ThrowExceptionForHR(endpoint.SetMute(false, Guid.Empty));
    }
}
"@

try {
    if (-not ('PaVolume' -as [type])) {
        Add-Type -TypeDefinition $source -ErrorAction Stop
    }
    $before = [math]::Round([PaVolume]::Get() * 100)
    [PaVolume]::Set($Level / 100.0)
    $after = [math]::Round([PaVolume]::Get() * 100)

    if ($before -eq $after) {
        Write-Host "  Volume: already $after per cent, and not muted."
    } else {
        Write-Host "  Volume: turned up from $before to $after per cent, and unmuted."
    }
    exit 0
} catch {
    Write-Host "  Volume: could not set it automatically. Set the Windows volume"
    Write-Host "          to $Level per cent by hand, and trim loudness at the amplifier."
    exit 0    # never stop the announcer starting over this
}
