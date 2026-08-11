const MAX_FILE_SIZE = 100 * 1024 * 1024;
const MAX_DURATION_SECONDS = 15 * 60;
const MP3_SAMPLE_BLOCK_SIZE = 1152;

const form = document.querySelector("#converter-form");
const fileInput = document.querySelector("#source-file");
const bitrateSelect = document.querySelector("#bitrate");
const convertButton = document.querySelector("#convert-button");
const progress = document.querySelector("#progress");
const status = document.querySelector("#status");
const download = document.querySelector("#download");

let downloadUrl = "";

function clearDownload() {
  if (downloadUrl) {
    URL.revokeObjectURL(downloadUrl);
    downloadUrl = "";
  }

  download.removeAttribute("href");
  download.hidden = true;
}

function setProgress(value, message) {
  progress.value = value;
  progress.hidden = false;
  status.textContent = message;
}

function convertSamples(samples, start, length) {
  const pcm = new Int16Array(length);

  for (let index = 0; index < length; index += 1) {
    const sample = Math.max(-1, Math.min(1, samples[start + index]));
    pcm[index] = sample < 0 ? sample * 32768 : sample * 32767;
  }

  return pcm;
}

fileInput.addEventListener("change", () => {
  clearDownload();
  progress.hidden = true;
  progress.value = 0;

  const file = fileInput.files?.[0];
  if (!file) {
    convertButton.disabled = true;
    status.textContent = "Select a file to begin.";
    return;
  }

  if (file.size > MAX_FILE_SIZE) {
    convertButton.disabled = true;
    status.textContent = "That file is over the 100 MB limit.";
    return;
  }

  convertButton.disabled = false;
  status.textContent = `${file.name} is ready to convert.`;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = fileInput.files?.[0];

  if (!file || file.size > MAX_FILE_SIZE) {
    return;
  }

  clearDownload();
  convertButton.disabled = true;
  fileInput.disabled = true;
  bitrateSelect.disabled = true;

  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  let audioContext;

  try {
    if (!AudioContextClass || !window.lamejs) {
      throw new Error("This browser does not support the converter.");
    }

    setProgress(2, "Reading your file...");
    const sourceData = await file.arrayBuffer();
    audioContext = new AudioContextClass({ sampleRate: 44100 });

    setProgress(8, "Decoding audio...");
    const audioBuffer = await audioContext.decodeAudioData(sourceData.slice(0));

    if (audioBuffer.duration > MAX_DURATION_SECONDS) {
      throw new Error("That file is longer than the 15-minute limit.");
    }

    const channelCount = Math.min(2, audioBuffer.numberOfChannels);
    if (channelCount < 1) {
      throw new Error("No audio track was found in that file.");
    }

    const left = audioBuffer.getChannelData(0);
    const right = channelCount === 2 ? audioBuffer.getChannelData(1) : null;
    const encoder = new window.lamejs.Mp3Encoder(
      channelCount,
      audioBuffer.sampleRate,
      Number(bitrateSelect.value),
    );
    const mp3Chunks = [];

    for (let start = 0; start < audioBuffer.length; start += MP3_SAMPLE_BLOCK_SIZE) {
      const length = Math.min(
        MP3_SAMPLE_BLOCK_SIZE,
        audioBuffer.length - start,
      );
      const leftChunk = convertSamples(left, start, length);
      const encoded = right
        ? encoder.encodeBuffer(
            leftChunk,
            convertSamples(right, start, length),
          )
        : encoder.encodeBuffer(leftChunk);

      if (encoded.length > 0) {
        mp3Chunks.push(new Int8Array(encoded));
      }

      const blockNumber = Math.floor(start / MP3_SAMPLE_BLOCK_SIZE);
      if (blockNumber % 40 === 0) {
        const percent = 10 + Math.round((start / audioBuffer.length) * 85);
        setProgress(percent, `Encoding MP3... ${percent}%`);
        await new Promise((resolve) => requestAnimationFrame(resolve));
      }
    }

    const finalChunk = encoder.flush();
    if (finalChunk.length > 0) {
      mp3Chunks.push(new Int8Array(finalChunk));
    }

    const mp3Blob = new Blob(mp3Chunks, { type: "audio/mpeg" });
    if (mp3Blob.size === 0) {
      throw new Error("The browser could not create an MP3 from that file.");
    }

    downloadUrl = URL.createObjectURL(mp3Blob);
    const baseName = file.name.replace(/\.[^/.]+$/, "") || "converted-audio";
    download.href = downloadUrl;
    download.download = `${baseName}.mp3`;
    download.textContent = `Download ${baseName}.mp3`;
    download.hidden = false;
    setProgress(100, "Conversion complete. Your MP3 is ready.");
  } catch (error) {
    progress.hidden = true;
    status.textContent =
      error instanceof Error && error.message
        ? error.message
        : "The browser could not convert that file.";
  } finally {
    if (audioContext) {
      await audioContext.close();
    }
    fileInput.disabled = false;
    bitrateSelect.disabled = false;
    convertButton.disabled =
      !fileInput.files?.[0] || fileInput.files[0].size > MAX_FILE_SIZE;
  }
});

window.addEventListener("pagehide", clearDownload);
