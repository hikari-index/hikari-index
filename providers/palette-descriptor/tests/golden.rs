//! Golden fixture tests for palette-descriptor.
//!
//! All images are generated in test setup — no binary fixtures in Git.

use std::process::Command;
use std::path::PathBuf;

fn binary_path() -> PathBuf {
    let mut path = std::env::current_exe().unwrap();
    path.pop();
    path.pop();
    path.push("palette-descriptor");
    path
}

fn make_solid_png(width: u32, height: u32, rgb: [u8; 3]) -> Vec<u8> {
    let mut buf = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut buf, width, height);
        encoder.set_color(png::ColorType::Rgb);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().unwrap();
        let mut data = Vec::with_capacity((width * height * 3) as usize);
        for _ in 0..(width * height) {
            data.extend_from_slice(&rgb);
        }
        writer.write_image_data(&data).unwrap();
    }
    buf
}

fn make_split_png(width: u32, height: u32, left: [u8; 3], right: [u8; 3]) -> Vec<u8> {
    let mut buf = Vec::new();
    {
        let mut encoder = png::Encoder::new(&mut buf, width, height);
        encoder.set_color(png::ColorType::Rgb);
        encoder.set_depth(png::BitDepth::Eight);
        let mut writer = encoder.write_header().unwrap();
        let mut data = Vec::with_capacity((width * height * 3) as usize);
        for y in 0..height {
            for x in 0..width {
                if x < width / 2 {
                    data.extend_from_slice(&left);
                } else {
                    data.extend_from_slice(&right);
                }
            }
        }
        writer.write_image_data(&data).unwrap();
    }
    buf
}

#[test]
fn solid_red_64x64() {
    let dir = std::env::temp_dir().join("palette_test_red");
    std::fs::create_dir_all(&dir).unwrap();
    let input = dir.join("red.png");
    let output = dir.join("red.json");
    let png_data = make_solid_png(64, 64, [255, 0, 0]);
    std::fs::write(&input, &png_data).unwrap();

    let status = Command::new(binary_path())
        .arg(&input)
        .arg(&output)
        .status()
        .expect("failed to run palette-descriptor");
    assert!(status.success(), "palette-descriptor exited with error");

    let json_str = std::fs::read_to_string(&output).unwrap();
    let v: serde_json::Value = serde_json::from_str(&json_str).unwrap();

    assert_eq!(v["descriptor_schema"], "hikari-color-descriptor/1");
    assert_eq!(v["provider"], "palette-descriptor");
    assert_eq!(v["provider_version"], "0.2.0");

    let dominant = v["palette"]["dominant"].as_array().unwrap();
    assert!(!dominant.is_empty());
    let d0 = &dominant[0];
    assert_eq!(d0["hex"], "#FF0000");
    assert_eq!(d0["rgb"], serde_json::json!([255, 0, 0]));
    assert!((d0["hue_deg"].as_f64().unwrap() - 0.0).abs() < 1e-3);
    assert!((d0["sat"].as_f64().unwrap() - 1.0).abs() < 1e-3);
    assert!((d0["luma"].as_f64().unwrap() - 0.2126).abs() < 1e-4);
    assert!((d0["weight"].as_f64().unwrap() - 1.0).abs() < 1e-3);

    // Red has luma 0.2126 < 0.33, so it should appear in shadows
    let shadows = v["palette"]["shadows"].as_array().unwrap();
    let shadow_hexes: Vec<&str> = shadows.iter().map(|s| s["hex"].as_str().unwrap()).collect();
    assert!(shadow_hexes.contains(&"#FF0000"), "red should be in shadows");

    // Red should NOT appear in midtones or highlights
    let midtones = v["palette"]["midtones"].as_array().unwrap();
    let mid_hexes: Vec<&str> = midtones.iter().map(|s| s["hex"].as_str().unwrap()).collect();
    assert!(!mid_hexes.contains(&"#FF0000"), "red should not be in midtones");

    let highlights = v["palette"]["highlights"].as_array().unwrap();
    let hi_hexes: Vec<&str> = highlights.iter().map(|s| s["hex"].as_str().unwrap()).collect();
    assert!(!hi_hexes.contains(&"#FF0000"), "red should not be in highlights");

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn all_black() {
    let dir = std::env::temp_dir().join("palette_test_black");
    std::fs::create_dir_all(&dir).unwrap();
    let input = dir.join("black.png");
    let output = dir.join("black.json");
    let png_data = make_solid_png(64, 64, [0, 0, 0]);
    std::fs::write(&input, &png_data).unwrap();

    let status = Command::new(binary_path())
        .arg(&input)
        .arg(&output)
        .status()
        .unwrap();
    assert!(status.success());

    let json_str = std::fs::read_to_string(&output).unwrap();
    let v: serde_json::Value = serde_json::from_str(&json_str).unwrap();

    assert_eq!(v["anchors"]["black"], true);
    assert_eq!(v["anchors"]["white"], false);
    assert!((v["luma"]["mean"].as_f64().unwrap() - 0.0).abs() < 1e-6);
    assert!((v["luma"]["p05"].as_f64().unwrap() - 0.0).abs() < 1e-6);
    assert!((v["luma"]["p50"].as_f64().unwrap() - 0.0).abs() < 1e-6);
    assert!((v["luma"]["p95"].as_f64().unwrap() - 0.0).abs() < 1e-6);

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn fifty_fifty_black_white() {
    let dir = std::env::temp_dir().join("palette_test_bw");
    std::fs::create_dir_all(&dir).unwrap();
    let input = dir.join("bw.png");
    let output = dir.join("bw.json");
    let png_data = make_split_png(64, 64, [0, 0, 0], [255, 255, 255]);
    std::fs::write(&input, &png_data).unwrap();

    let status = Command::new(binary_path())
        .arg(&input)
        .arg(&output)
        .status()
        .unwrap();
    assert!(status.success());

    let json_str = std::fs::read_to_string(&output).unwrap();
    let v: serde_json::Value = serde_json::from_str(&json_str).unwrap();

    let rep = v["palette"]["representative"].as_array().unwrap();
    let rep_hexes: Vec<&str> = rep.iter().map(|s| s["hex"].as_str().unwrap()).collect();
    assert_eq!(rep_hexes, vec!["#000000", "#FFFFFF"], "representative should be [black, white] in stable order");

    assert_eq!(v["anchors"]["black"], true);
    assert_eq!(v["anchors"]["white"], true);

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn fifty_fifty_red_blue() {
    let dir = std::env::temp_dir().join("palette_test_rb");
    std::fs::create_dir_all(&dir).unwrap();
    let input = dir.join("rb.png");
    let output = dir.join("rb.json");
    let png_data = make_split_png(64, 64, [255, 0, 0], [0, 0, 255]);
    std::fs::write(&input, &png_data).unwrap();

    let status = Command::new(binary_path())
        .arg(&input)
        .arg(&output)
        .status()
        .unwrap();
    assert!(status.success());

    let json_str = std::fs::read_to_string(&output).unwrap();
    let v: serde_json::Value = serde_json::from_str(&json_str).unwrap();

    let dominant = v["palette"]["dominant"].as_array().unwrap();
    assert!(dominant.len() >= 2, "red-blue split should have >= 2 dominant entries");
    let dom_hexes: Vec<&str> = dominant.iter().map(|s| s["hex"].as_str().unwrap()).collect();
    assert!(dom_hexes.contains(&"#FF0000"), "red should be dominant");
    assert!(dom_hexes.contains(&"#0000FF"), "blue should be dominant");

    let bands = v["hue_family"]["bands"].as_array().unwrap();
    assert!(bands.len() == 8);
    // Band 0 (0 deg, red) should have mass; band 4/5 (220/275, blue neighbours) should have mass
    assert!(bands[0].as_f64().unwrap() > 0.0, "red band should have mass");

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn corrupt_input_fails() {
    let dir = std::env::temp_dir().join("palette_test_corrupt");
    std::fs::create_dir_all(&dir).unwrap();
    let input = dir.join("corrupt.png");
    let output = dir.join("corrupt.json");
    std::fs::write(&input, b"not a png").unwrap();

    let cmd_output = Command::new(binary_path())
        .arg(&input)
        .arg(&output)
        .output()
        .unwrap();

    assert!(!cmd_output.status.success(), "corrupt input should fail");
    assert!(!cmd_output.stderr.is_empty(), "should have stderr message");
    assert!(!output.exists(), "output file should not exist on failure");

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn determinism() {
    let dir = std::env::temp_dir().join("palette_test_determ");
    std::fs::create_dir_all(&dir).unwrap();
    let input = dir.join("determ.png");
    let output1 = dir.join("determ1.json");
    let output2 = dir.join("determ2.json");
    let png_data = make_split_png(64, 64, [128, 64, 32], [32, 128, 64]);
    std::fs::write(&input, &png_data).unwrap();

    let s1 = Command::new(binary_path())
        .arg(&input)
        .arg(&output1)
        .status()
        .unwrap();
    assert!(s1.success());

    let s2 = Command::new(binary_path())
        .arg(&input)
        .arg(&output2)
        .status()
        .unwrap();
    assert!(s2.success());

    let bytes1 = std::fs::read(&output1).unwrap();
    let bytes2 = std::fs::read(&output2).unwrap();
    assert_eq!(bytes1, bytes2, "output must be byte-identical across runs");

    std::fs::remove_dir_all(&dir).ok();
}
