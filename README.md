# STM32 RLC Control Interface

This GUI talks to the STM32 firmware over USART2 at 115200 baud.

## Run

Double-click:

```text
run_gui.bat
```

The batch file creates a local `.venv`, installs `pyserial`, and starts the GUI.

## Data Format

The Python GUI uses CSV/text output.

The firmware sends:

```text
DATA,time_us,adc_mV,dac_mV
```

- `adc_mV`: measured voltage on PA5
- `dac_mV`: command voltage written to PA4 / DAC channel 1

## Typical Live Test

1. Select the STM32 COM port.
2. Click `Connect`.
3. Choose `SQUARE`.
4. Set:
   - `Amp mV`: `1300`
   - `Offset mV`: `0`
   - `Freq Hz`: `20`
   - `Duty %`: `50`
   - `Ts us`: `1000`
5. Click `Start Live`.

Use `Stop` to stop streaming.

## Plot Controls

- `Zoom In`: show a smaller, more detailed recent time window
- `Zoom Out`: widen the visible time window
- `Reset Zoom`: return to the full buffered graph
- Drag on the graph to zoom into a selected time region
- Mouse wheel over the graph also zooms

The graph header shows the latest real values:

```text
t, ADC mV, DAC mV, error mV, zoom level, visible window
```

## Wave Modes

- `DC`: constant `Offset + Amp`
- `STEP`: constant high level, useful for capture mode
- `SQUARE`: periodic 50% duty wave
- `PULSE`: periodic wave using the `Duty %` field
- `IMPULSE`: one-sample high pulse at the start of each period
- `SINE`: unipolar sine from `Offset` to `Offset + Amp`
- `TRIANGLE`: unipolar triangle from `Offset` to `Offset + Amp`

## Fast Capture Test

For a fast step response:

- `Wave`: `STEP`
- `Amp mV`: `1300`
- `Ts us`: `10`
- `Samples`: `1000`

Then click `Start Capture`.

Capture mode records first, then sends the data back to the GUI.

## Simulink Binary Mode

For Simulink models that expect the old binary burst format, send:

```text
START,SIMULINK
```

`START,BINLIVE` is kept as an alias for the same burst mode.

The Simulink mode preserves the old burst behavior:

- apply one DAC level
- capture 300 fast ADC samples
- transmit 300 binary `float[2]` frames
- advance to the next waveform level
- repeat continuously

```text
[reference_voltage_V, measured_voltage_V]
```

This matches the old Simulink-style payload. Waveform generation is controlled by the current settings:

```text
SET,WAVE,SINE
SET,AMP,1000
SET,OFFSET,500
SET,FREQ,5
SET,DUTY,20
START,SIMULINK
```

In this mode the Python GUI should not try to plot the data because the stream is binary, not CSV text.
