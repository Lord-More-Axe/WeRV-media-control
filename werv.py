from flask import Flask, render_template, request, make_response, current_app, redirect, url_for, flash, session, logging, jsonify, abort

app = Flask(__name__)

@app.route("/")
def home():
    return render_template('main.html')

if __name__ == '__main__':
    app.run(debug=True)