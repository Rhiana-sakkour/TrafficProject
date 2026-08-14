import numpy as np

CAMERA_HEIGHT = 8.0
CAMERA_PITCH  = -45.0
CAMERA_FOV    = 60.0
IMG_WIDTH     = 1280
IMG_HEIGHT    = 720

def calculate_pixels_per_meter():
    pitch_rad = abs(np.radians(CAMERA_PITCH))
    fov_half  = np.radians(CAMERA_FOV) / 2

    # PPM الرأسي — للإزاحة على طول الطريق (يُستخدم في حساب السرعة)
    d_far         = CAMERA_HEIGHT / np.tan(pitch_rad - fov_half)  # 22.39 m
    d_near        = CAMERA_HEIGHT / np.tan(pitch_rad + fov_half)  # 1.61 m
    ground_length = d_far - d_near                                 # 20.78 m
    ppm_vertical  = IMG_HEIGHT / ground_length                     # 34.65

    # PPM الأفقي — للعرض فقط (للمعلومات)
    d_center       = CAMERA_HEIGHT / np.tan(pitch_rad)
    w_ground       = 2 * d_center * np.tan(fov_half)
    ppm_horizontal = IMG_WIDTH / w_ground                          # 184.7

    print(f"d_far          = {d_far:.2f} m")
    print(f"d_near         = {d_near:.2f} m")
    print(f"ground_length  = {ground_length:.2f} m")
    print(f"PPM_vertical   = {ppm_vertical:.2f}  ← ضعها في traffic_analyzer.py")
    print(f"PPM_horizontal = {ppm_horizontal:.2f} (للمعلومات فقط)")
    return ppm_vertical

if __name__ == '__main__':
    calculate_pixels_per_meter()