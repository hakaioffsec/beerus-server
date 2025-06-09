from flask import Flask, request, jsonify
import os
import tarfile
import shutil

app = Flask(__name__)

UPLOAD_FOLDER = 'uploads'
EXTRACT_FOLDER = 'apps'
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
        print("Nenhum arquivo enviado")
        return jsonify({'error': 'Nenhum arquivo enviado'}), 400

    file = request.files['file']

    if file.filename == '':
        print("Nome do arquivo inválido")
        return jsonify({'error': 'Nenhum arquivo selecionado'}), 400

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
                'message': f'Arquivo {filename} recebido e extraído em {extract_path}'
            })

        except Exception as e:
            return jsonify({'error': f'Erro ao extrair o arquivo: {str(e)}'}), 500

    print("Tipo de arquivo não permitido")
    return jsonify({'error': 'Tipo de arquivo não permitido'}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=6524)
