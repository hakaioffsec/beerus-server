from flask import Flask, request, jsonify
import os
import tarfile
import shutil

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
EXTRACT_FOLDER = 'data'
ALLOWED_EXTENSIONS = {'tar.gz'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(EXTRACT_FOLDER, exist_ok=True)

def allowed_file(filename):
    for ext in ALLOWED_EXTENSIONS:
        if filename.endswith(ext):
            return True
    return False

@app.route('/', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        print("No file sent")
        return jsonify({'error': 'No file sent'}), 400

    file = request.files['file']

    if file.filename == '':
        print("Invalid filename")
        return jsonify({'error': 'No file selected'}), 400

    if file and allowed_file(file.filename):
        filename = file.filename
        filepath = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(filepath)

        try:
            base_name = os.path.splitext(os.path.splitext(filename)[0])[0]
            extract_path = os.path.join(EXTRACT_FOLDER, base_name)

            os.makedirs(extract_path, exist_ok=True)

            with tarfile.open(filepath, 'r:gz') as tar:
                tar.extractall(path=extract_path)

            return jsonify({
                'success': True,
                'message': f'File {filename} received and extracted to {extract_path}'
            })

        except Exception as e:
            return jsonify({'error': f'Error extracting file: {str(e)}'}), 500

    print("File type not allowed")
    return jsonify({'error': 'File type not allowed'}), 400

@app.route('/check', methods=['GET'])
def checkServer():
    return jsonify({'app': 'Beerus Server', 'version': '1.0'}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=6524)