//! palette-descriptor CLI — deterministic colour descriptor for one candidate still.
//!
//! Usage: palette-descriptor <input.png> <output.json> [--max-samples N]

mod color;
mod decode;
mod palette;

use std::path::PathBuf;
use std::process::ExitCode;

use crate::decode::DecodedImage;
use crate::palette::{build_descriptor, extract_palettes};

const DEFAULT_MAX_SAMPLES: usize = 4_000_000;

fn identity() -> String {
    format!("{}-{}", env!("CARGO_PKG_NAME"), env!("CARGO_PKG_VERSION"))
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    let mut input: Option<PathBuf> = None;
    let mut output: Option<PathBuf> = None;
    let mut max_samples: usize = DEFAULT_MAX_SAMPLES;
    let mut identity_mode = false;

    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--identity" => {
                identity_mode = true;
            }
            "--max-samples" => {
                i += 1;
                if i >= args.len() {
                    eprintln!("error: --max-samples requires a value");
                    return ExitCode::FAILURE;
                }
                match args[i].parse::<usize>() {
                    Ok(n) => max_samples = n,
                    Err(_) => {
                        eprintln!("error: invalid --max-samples value: {}", args[i]);
                        return ExitCode::FAILURE;
                    }
                }
            }
            s if s.starts_with("--max-samples=") => {
                let val = &s["--max-samples=".len()..];
                match val.parse::<usize>() {
                    Ok(n) => max_samples = n,
                    Err(_) => {
                        eprintln!("error: invalid --max-samples value: {val}");
                        return ExitCode::FAILURE;
                    }
                }
            }
            s if s.starts_with('-') => {
                eprintln!("error: unknown option: {s}");
                return ExitCode::FAILURE;
            }
            _ => {
                if input.is_none() {
                    input = Some(PathBuf::from(&args[i]));
                } else if output.is_none() {
                    output = Some(PathBuf::from(&args[i]));
                } else {
                    eprintln!("error: unexpected argument: {}", args[i]);
                    return ExitCode::FAILURE;
                }
            }
        }
        i += 1;
    }

    if identity_mode {
        println!("{}", identity());
        return ExitCode::SUCCESS;
    }

    let input = match input {
        Some(p) => p,
        None => {
            eprintln!("error: usage: palette-descriptor <input.png> <output.json> [--max-samples N] [--identity]");
            return ExitCode::FAILURE;
        }
    };
    let output = match output {
        Some(p) => p,
        None => {
            eprintln!("error: usage: palette-descriptor <input.png> <output.json> [--max-samples N] [--identity]");
            return ExitCode::FAILURE;
        }
    };

    match run(&input, &output, max_samples) {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            eprintln!("error: {e}");
            ExitCode::FAILURE
        }
    }
}

fn run(input: &std::path::Path, output: &std::path::Path, max_samples: usize) -> Result<(), String> {
    let img = DecodedImage::load(input)?;
    let (samples, stride, sampled_pixels) = img.samples(max_samples);

    let extracted = extract_palettes(&samples, 8);
    if extracted.representative.is_empty() {
        return Err("no usable palette colors found after black/white filtering".into());
    }

    let descriptor = build_descriptor(&extracted, &samples, max_samples, stride, sampled_pixels);

    let json = serde_json::to_string_pretty(&descriptor)
        .map_err(|e| format!("JSON serialization failed: {e}"))?;
    std::fs::write(output, json.as_bytes())
        .map_err(|e| format!("{}: {e}", output.display()))?;
    Ok(())
}
