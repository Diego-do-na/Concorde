use std::env;
use std::fs;
use std::process;

use serde_json::json;

use concorde_api::audio::vad::{detect_turns, Turn, Turns, VadParams};

fn print_usage_and_exit() -> ! {
    eprintln!("usage: vad-dump <in.wav> [--params k=v ...]");
    process::exit(2);
}

fn parse_params(args: &[String]) -> VadParams {
    let mut params = VadParams::default();
    for kv in args {
        if let Some((k, v)) = kv.split_once('=') {
            match k {
                "frame_ms" => params.frame_ms = v.parse().unwrap_or(params.frame_ms),
                "hop_ms" => params.hop_ms = v.parse().unwrap_or(params.hop_ms),
                "noise_floor_percentile" => {
                    params.noise_floor_percentile = v.parse().unwrap_or(params.noise_floor_percentile)
                }
                "threshold_db_above_floor" => {
                    params.threshold_db_above_floor = v.parse().unwrap_or(params.threshold_db_above_floor)
                }
                "on_frames" => params.on_frames = v.parse().unwrap_or(params.on_frames),
                "off_frames" => params.off_frames = v.parse().unwrap_or(params.off_frames),
                "min_speech_ms" => params.min_speech_ms = v.parse().unwrap_or(params.min_speech_ms),
                "min_gap_ms" => params.min_gap_ms = v.parse().unwrap_or(params.min_gap_ms),
                _ => {
                    eprintln!("warning: unknown param {k}");
                }
            }
        } else {
            eprintln!("warning: malformed param {kv}, expected k=v");
        }
    }
    params
}

fn main() {
    let mut args: Vec<String> = env::args().skip(1).collect();
    if args.is_empty() {
        print_usage_and_exit();
    }
    let infile = args.remove(0);

    // collect any --params and following k=v items
    let mut params_args: Vec<String> = Vec::new();
    let mut i = 0;
    while i < args.len() {
        if args[i] == "--params" {
            i += 1;
            while i < args.len() && !args[i].starts_with("--") {
                params_args.push(args[i].clone());
                i += 1;
            }
        } else {
            i += 1;
        }
    }

    let params = parse_params(&params_args);

    let wav = match fs::read(&infile) {
        Ok(b) => b,
        Err(e) => {
            eprintln!("failed to read {}: {}", infile, e);
            process::exit(1);
        }
    };

    match detect_turns(&wav, &params) {
        Ok(turns) => {
            // Print exactly the dataset shape: {"turns":[{...}, ...]}
            let out = json!(turns);
            println!("{}", serde_json::to_string(&out).unwrap());
        }
        Err(e) => {
            eprintln!("vad error: {}", e);
            process::exit(1);
        }
    }
}

