use std::env;
use std::fs;
use std::io::Cursor;
use std::path::Path;
use std::process;

use anyhow::{Context, Result};
use serde_json::json;

use concorde_api::audio::vad::{detect_turns, VadParams};
use concorde_api::semantic::{asr, probe, rules};

fn print_usage_and_exit() -> ! {
    eprintln!("usage: semantic-dump <in.wav> [--whisper-model <path>] [--probe-templates <path>]");
    process::exit(2);
}

fn wav_channels(wav: &[u8]) -> Result<(u32, Vec<f32>, Vec<f32>)> {
    let mut reader = hound::WavReader::new(Cursor::new(wav)).context("not a valid WAV file")?;
    let spec = reader.spec();
    let sr = spec.sample_rate;
    let num_channels = spec.channels as usize;
    if num_channels == 0 {
        anyhow::bail!("WAV has 0 channels");
    }

    let mut ch0 = Vec::new();
    let mut ch1 = Vec::new();

    match spec.sample_format {
        hound::SampleFormat::Int => {
            let max_val = (1i64 << (spec.bits_per_sample - 1)) as f32;
            let samples: Vec<i32> = reader.samples::<i32>().collect::<Result<_, _>>()?;
            for chunk in samples.chunks(num_channels) {
                if chunk.len() == num_channels {
                    let s0 = chunk[0] as f32 / max_val;
                    let s1 = if num_channels > 1 { chunk[1] as f32 / max_val } else { s0 };
                    ch0.push(s0);
                    ch1.push(s1);
                }
            }
        }
        hound::SampleFormat::Float => {
            let samples: Vec<f32> = reader.samples::<f32>().collect::<Result<_, _>>()?;
            for chunk in samples.chunks(num_channels) {
                if chunk.len() == num_channels {
                    let s0 = chunk[0];
                    let s1 = if num_channels > 1 { chunk[1] } else { s0 };
                    ch0.push(s0);
                    ch1.push(s1);
                }
            }
        }
    }

    Ok((sr, ch0, ch1))
}

fn main() {
    let mut args: Vec<String> = env::args().skip(1).collect();
    if args.is_empty() {
        print_usage_and_exit();
    }
    let infile = args.remove(0);

    let mut whisper_path = env::var("CONCORDE_WHISPER_MODEL_PATH").ok();
    let mut probe_path = env::var("CONCORDE_PROBE_TEMPLATES_PATH").ok();

    let mut i = 0;
    while i < args.len() {
        if args[i] == "--whisper-model" && i + 1 < args.len() {
            whisper_path = Some(args[i + 1].clone());
            i += 2;
        } else if args[i] == "--probe-templates" && i + 1 < args.len() {
            probe_path = Some(args[i + 1].clone());
            i += 2;
        } else {
            i += 1;
        }
    }

    let wav = match fs::read(&infile) {
        Ok(b) => b,
        Err(e) => {
            eprintln!("failed to read {}: {}", infile, e);
            process::exit(1);
        }
    };

    let (sample_rate, ch0, ch1) = match wav_channels(&wav) {
        Ok(res) => res,
        Err(e) => {
            eprintln!("failed to decode WAV: {}", e);
            process::exit(1);
        }
    };

    let turns = match detect_turns(&wav, &VadParams::default()) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("VAD failed: {}", e);
            process::exit(1);
        }
    };

    let probe_templates_file = probe_path.unwrap_or_else(|| {
        if Path::new("artifacts/probe_templates.json").exists() {
            "artifacts/probe_templates.json".to_string()
        } else if Path::new("../../product/artifacts/probe_templates.json").exists() {
            "../../product/artifacts/probe_templates.json".to_string()
        } else {
            "product/artifacts/probe_templates.json".to_string()
        }
    });

    let probe_detector = match probe::ProbeDetector::load(Path::new(&probe_templates_file)) {
        Ok(pd) => pd,
        Err(e) => {
            eprintln!("failed to load probe templates from {}: {}", probe_templates_file, e);
            process::exit(1);
        }
    };

    let probe_match = probe_detector.detect(&ch1, &turns.turns);

    let mut probe_detected = false;
    let mut probe_t = 0.0f64;
    let mut answer_type = String::new();
    let mut invention_score = 0.5f64;

    if let Some(pm) = probe_match {
        probe_detected = true;
        probe_t = pm.start as f64;

        if let Some((_answer_gi, start, end)) = probe::find_answer_turn(&turns.turns, pm.turn_index) {
            if let Some(ref w_path) = whisper_path {
                if Path::new(w_path).exists() {
                    let threads = env::var("CONCORDE_WHISPER_THREADS")
                        .ok()
                        .and_then(|s| s.parse().ok())
                        .unwrap_or(4);
                    if let Ok(local_asr) = asr::LocalAsr::load(w_path, threads) {
                        let s = (start as f64 * sample_rate as f64).round().max(0.0) as usize;
                        let e = ((end as f64 * sample_rate as f64).round() as usize).min(ch0.len());
                        let answer_samples: Vec<f32> = if e > s { ch0[s..e].to_vec() } else { Vec::new() };
                        let prepared = asr::prepare_for_asr(&answer_samples, sample_rate);

                        use concorde_api::semantic::asr::Transcriber;
                        if let Ok(text) = local_asr.transcribe(&prepared) {
                            let (atype, score) = rules::analyze_answer(&text);
                            answer_type = atype.as_str().to_string();
                            invention_score = score;
                        }
                    }
                }
            }
        }
    }

    let out = json!({
        "probe_detected": probe_detected,
        "probe_t": probe_t,
        "answer_type": answer_type,
        "invention_score": invention_score,
    });

    println!("{}", serde_json::to_string(&out).unwrap());
}
