# Training the "Hey Friday" wake word

Until this is done, the listener uses the ready-made "Hey Jarvis" model. This guide trains your own
"Hey Friday" model on Google Colab (free, with a GPU), then uses it on the laptop.

**Time:** about 1 to 1.5 hours, mostly waiting. **You need:** a Google account and a Chrome or Edge browser.
**No recordings of your voice are needed.** The notebook makes thousands of "hey friday" clips with
computer voices, mixes them with background noise, and trains a small model on them.

---

## Part 1: open the notebook (2 minutes)

1. Open this link in the browser:
   https://colab.research.google.com/github/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb
   It is openWakeWord's own training notebook (the same project our listener uses).
2. Top menu: **Runtime → Change runtime type → T4 GPU → Save**.
   Without the GPU, making the clips takes many hours.
3. Top menu: **File → Save a copy in Drive**. You now edit your own copy.

## Part 2: set the phrase (1 minute)

Scroll to the code cell that starts with `# Modify values in the config and save a new version`.
Replace the whole cell with this, exactly:

```python
# Modify values in the config and save a new version
config["target_phrase"] = ["hey friday"]
config["model_name"] = "hey_friday"
config["n_samples"] = 3000          # clips of "hey friday" (the example uses 1000; more = better, slower)
config["n_samples_val"] = 1000      # clips kept aside to check the model
config["steps"] = 15000             # training steps (the example uses 10000)
config["target_accuracy"] = 0.6
config["target_recall"] = 0.25

config["background_paths"] = ['./audioset_16k', './fma']
config["false_positive_validation_data_path"] = "validation_set_features.npy"
config["feature_data_files"] = {"ACAV100M_sample": "openwakeword_features_ACAV100M_2000_hrs_16bit.npy"}

with open('my_model.yaml', 'w') as file:
    documents = yaml.dump(config, file)
```

In a hurry? Keep the notebook's own numbers (`n_samples = 1000`, `steps = 10000`). It trains faster and
works, but it triggers less reliably.

## Part 3: run it (about 1 hour)

1. Top menu: **Runtime → Run all**. If Colab asks "Run anyway?", choose **Run anyway**.
2. Leave the tab open. The long parts:
   - downloading the background sounds (a few GB, several minutes)
   - **Step 1, Generate synthetic clips**: about 10 minutes per 1000 clips on the T4 GPU
   - **Step 3, Train model**: the longest wait
3. If a cell stops with an error during **Step 1**, just run that cell again: it carries on where it
   stopped. Any other error: copy the red text and send it to me.
4. If the **Step 4** cell says the `.tflite` file failed, ignore it. We only need the `.onnx` file.

## Part 4: download the model (1 minute)

1. Click the **folder icon** on the left side of Colab.
2. Open the folder **my_custom_model**.
3. Right-click **hey_friday.onnx → Download**.

## Part 5: use it on the laptop (5 minutes)

1. Put the file here (create the `models` folder if it is missing):
   `C:\project folder\kraken-001\backend\models\hey_friday.onnx`
   (`models/` is not in git, so the file stays on your laptop.)
2. In `backend\.env`:
   ```
   WAKE_MODEL=models/hey_friday.onnx
   WAKE_THRESHOLD=0.5
   ```
3. Test only the wake word first (no API needed), from `backend\`:
   ```powershell
   python -m voice.run_listener --ack-only
   ```
   Say "Hey Friday" a few times at normal distance. Each time it should answer "Yes, boss" and print
   `wake word heard (score ...)`.
4. Tune it:
   - Nothing happens at all: run `python -m voice.run_listener --debug` and watch the `mic` bar while you talk.
     No bar = wrong microphone (`--list-devices`, then set `WAKE_DEVICE`).
   - It misses you often: lower the threshold, `WAKE_THRESHOLD=0.4` (or `--threshold 0.4` to try quickly).
   - It wakes up when nobody said it (talking, TV, clapping): raise it, `WAKE_THRESHOLD=0.6`.
   - Still poor after tuning: train again with `n_samples = 5000` and `steps = 25000`.
5. Then run the full Friday (API running, `FRIDAY_USER` set):
   ```powershell
   python -m voice.run_listener
   ```

If the file is not where `WAKE_MODEL` points, the listener stops at once and says where it looked. To go
back to the stand-in, set `WAKE_MODEL=hey_jarvis_v0.1`.

---

## Disclosure (already in docs/disclosures.md)

Trained on Google Colab with openWakeWord's automatic training notebook (code Apache-2.0). The training
data is synthetic speech from piper-sample-generator (LibriTTS-R voice) plus background audio and
features from AudioSet, the Free Music Archive and ACAV100M; their own licences apply. The ready-made
"Hey Jarvis" model is CC BY-NC-SA 4.0 (non-commercial use only).
