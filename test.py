import mysql.connector

conexion = mysql.connector.connect(
    host="localhost",
    user="root",       # o tu usuario
    password="12345678",       # tu password
    database="login_db"
)

cursor = conexion.cursor()

username = "admin"
password = "admin"

print("Probando con:", username, password)

cursor.execute("SELECT * FROM usuarios WHERE username=%s AND password=%s", (username, password))
user = cursor.fetchone()

print("Resultado SQL:", user)

if user:
    print("✅ Login correcto")
else:
    print("❌ Login incorrecto")
