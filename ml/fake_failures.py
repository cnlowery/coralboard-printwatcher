"""Write G-code that makes 'fake' print failures for training: at several spots over the bed the head
circles while extruding into the air, so a spaghetti nest forms and drops onto the bed, like a print
that came loose. Each spot ends with a pause so the camera sees the nest with the head still.

M118 lines ("PW spot N start/end") go to the Klipper console with timestamps, so frames can be matched
to spots afterwards (Moonraker /server/gcode_store).

  Needs the printer already homed (no G28: Elegoo wraps it in a macro; unhomed moves just error out).
  python fake_failures.py --temp 245 -o fake_failures_petg.gcode   (PETG; use 215 for PLA)
Upload with Moonraker and start it like a print, so Fluidd's Cancel works. The bed must be clear.
Untested estimate: about 6 min and 400 mm of filament (about 1.2 g PLA/PETG).
"""
import argparse

# (x, y, z) in mm. Spread over the bed and over heights the camera sees; bed is 235 x 235.
SPOTS = [(117, 117, 20), (60, 60, 15), (175, 60, 25), (60, 175, 35), (175, 175, 20), (117, 45, 40)]
SIDE = 15        # mm, side of the square the head circles
LOOPS = 15       # squares per spot (about 3 s each at F1200)
E_PER_MM = 0.075 # mm of filament per mm of travel: about 1.5 mm/s of filament at 20 mm/s, a bit more than printing
HOLD_S = 15      # head still after each spot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--temp", type=int, required=True, help="nozzle temperature")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    g = ["; PrintWatch fake failures (ml/fake_failures.py)", "G90", "M83",
         f"M118 PW heating to {a.temp}", f"M109 S{a.temp}", "M106 S255", "G1 Z50 F600"]
    e = round(SIDE * E_PER_MM, 3)
    for n, (x, y, z) in enumerate(SPOTS, 1):
        g += [f"G1 Z{max(z, 50)} F600", f"G1 X{x} Y{y} F6000", f"G1 Z{z} F600", f"M118 PW spot {n} start x{x} y{y} z{z}"]
        for _ in range(LOOPS):
            g += [f"G1 X{x + SIDE} Y{y} E{e} F1200", f"G1 X{x + SIDE} Y{y + SIDE} E{e} F1200",
                  f"G1 X{x} Y{y + SIDE} E{e} F1200", f"G1 X{x} Y{y} E{e} F1200"]
        g += ["G1 E-1 F1800", f"G4 S{HOLD_S}", f"M118 PW spot {n} end"]
    g += ["M104 S0", "M107", "G1 Z80 F600", "G1 X10 Y225 F6000", "M84 E", "M118 PW done"]
    open(a.out, "w", newline="\n").write("\n".join(g) + "\n")


if __name__ == "__main__":
    main()
