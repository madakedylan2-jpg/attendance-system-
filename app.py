    file = request.files["photo"]
    try:
        from PIL import Image
        import zxingcpp
        import io

        image = Image.open(io.BytesIO(file.read())).convert("RGB")
        results = zxingcpp.read_barcodes(image)
    except ImportError:
        return jsonify(
            ok=False,
            message="Server-side photo decoding isn't installed. Run: pip install zxing-cpp Pillow",
        ), 500
    except Exception as e:
        return jsonify(ok=False, message=f"Couldn't read that image: {e}"), 400

    if not results:
        return jsonify(
            ok=False,
            message="Couldn't find a barcode in that photo — try better lighting/focus, "
                    "make sure the barcode fills more of the frame, or use manual entry below.",
        ), 200

    barcode_value = results[0].text
