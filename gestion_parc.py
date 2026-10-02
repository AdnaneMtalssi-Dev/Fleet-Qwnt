import sqlite3
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from tkinter import *
from tkinter import ttk, messagebox, simpledialog, filedialog
from PIL import Image, ImageTk

# ============================================
# CLASSE HELPER : SCROLLABLE FRAME
# ============================================
class ScrollableFrame(Frame):
    def __init__(self, container, *args, **kwargs):
        super().__init__(container, *args, **kwargs)
        canvas = Canvas(self, bg=kwargs.get('bg', '#f4f6f9'), highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.scrollable_frame = Frame(canvas, bg=kwargs.get('bg', '#f4f6f9'))

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        
        # Binding souris pour scroll
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")
        canvas.bind_all("<MouseWheel>", _on_mousewheel)

# ============================================
# CONFIGURATION
# ============================================
DB_FILE = "flotte_location.db"
BACKUP_DIR = "backups"
PHOTOS_DIR = "photos_vehicules"

# Créer les dossiers nécessaires
os.makedirs(BACKUP_DIR, exist_ok=True)
os.makedirs(PHOTOS_DIR, exist_ok=True)

# ============================================
# BASE DE DONNÉES SQLITE
# ============================================
def _add_column_if_missing(conn, table, column, definition):
    """Migration légère : ajoute une colonne si absente (compatible anciennes bases)."""
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

def init_db():
    """Crée/migre la base de données (photos, statut véhicule, retour, notes...)."""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("PRAGMA foreign_keys = ON;") # Activation des clés étrangères
    c = conn.cursor()
    
    # Table Véhicules (avec colonne photo)
    c.execute('''
        CREATE TABLE IF NOT EXISTS vehicules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marque TEXT NOT NULL,
            modele TEXT NOT NULL,
            immatriculation TEXT UNIQUE NOT NULL,
            categorie TEXT DEFAULT 'Economique',
            prix_jour REAL DEFAULT 0,
            date_assurance TEXT,
            date_visite TEXT,
            date_agrement TEXT,
            photo_path TEXT,
            date_ajout TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Table Locations
    c.execute('''
        CREATE TABLE IF NOT EXISTS locations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_location TEXT NOT NULL,
            vehicule_id INTEGER NOT NULL,
            client TEXT NOT NULL,
            date_debut TEXT NOT NULL,
            date_fin TEXT NOT NULL,
            jours INTEGER NOT NULL,
            prix_jour REAL NOT NULL,
            total REAL NOT NULL,
            paiement TEXT DEFAULT 'Espèces',
            FOREIGN KEY (vehicule_id) REFERENCES vehicules(id) ON DELETE CASCADE
        )
    ''')

    # Table Charges Fixes
    c.execute('''
        CREATE TABLE IF NOT EXISTS charges_fixes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_charge TEXT NOT NULL,
            vehicule_id INTEGER,
            type TEXT NOT NULL,
            montant REAL NOT NULL,
            periode TEXT,
            mois TEXT,
            FOREIGN KEY (vehicule_id) REFERENCES vehicules(id) ON DELETE SET NULL
        )
    ''')

    # Table Charges Variables
    c.execute('''
        CREATE TABLE IF NOT EXISTS charges_variables (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_charge TEXT NOT NULL,
            vehicule_id INTEGER,
            type TEXT NOT NULL,
            montant REAL NOT NULL,
            description TEXT,
            FOREIGN KEY (vehicule_id) REFERENCES vehicules(id) ON DELETE SET NULL
        )
    ''')

    # Table Personnel
    c.execute('''
        CREATE TABLE IF NOT EXISTS personnel (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date_paie TEXT NOT NULL,
            montant REAL NOT NULL,
            description TEXT
        )
    ''')

    # --- Migrations douces pour les bases existantes ---
    _add_column_if_missing(conn, 'vehicules', 'statut', "TEXT DEFAULT 'Disponible'")
    _add_column_if_missing(conn, 'vehicules', 'kilometrage', "REAL")
    _add_column_if_missing(conn, 'locations', 'date_retour_reelle', "TEXT")
    _add_column_if_missing(conn, 'locations', 'acompte', "REAL DEFAULT 0")
    _add_column_if_missing(conn, 'locations', 'notes', "TEXT")
    _add_column_if_missing(conn, 'personnel', 'nom', "TEXT")
    _add_column_if_missing(conn, 'personnel', 'poste', "TEXT")

    # Index d'performance
    c.execute('CREATE INDEX IF NOT EXISTS idx_loc_vehicule ON locations(vehicule_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_loc_dates ON locations(date_debut, date_fin)')

    # Statistiques mensuelles (6 derniers mois) — revenus vs charges
    try:
        conn.execute("DROP VIEW IF EXISTS stats_mensuelles")
        conn.execute('''
            CREATE VIEW stats_mensuelles AS
            SELECT mois,
                   SUM(revenus)  AS revenus,
                   SUM(charges)  AS charges,
                   SUM(revenus) - SUM(charges) AS resultat
            FROM (
                SELECT substr(date_debut, 1, 7) AS mois, total AS revenus, 0 AS charges FROM locations
                UNION ALL
                SELECT substr(date_charge, 1, 7), 0, montant FROM charges_fixes
                UNION ALL
                SELECT substr(date_charge, 1, 7), 0, montant FROM charges_variables
                UNION ALL
                SELECT substr(date_paie, 1, 7), 0, montant FROM personnel
            )
            GROUP BY mois
            ORDER BY mois DESC
            LIMIT 6
        ''')
    except Exception:
        pass

    conn.commit()
    conn.close()
    print(f"✅ Base de données prête : {os.path.abspath(DB_FILE)}")

# ============================================
# BACKUP AUTOMATIQUE
# ============================================
def create_backup():
    """Crée une sauvegarde cohérente (API sqlite3 Backup, pas de copie à chaud)."""
    if not os.path.exists(DB_FILE):
        return
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    backup_name = f"flotte_backup_{timestamp}.db"
    backup_path = os.path.join(BACKUP_DIR, backup_name)

    try:
        src = sqlite3.connect(DB_FILE)
        dst = sqlite3.connect(backup_path)
        with dst:
            src.backup(dst)
        dst.close()
        src.close()
    except Exception as e:
        print(f"⚠️ Échec du backup : {e}")
        return None
    clean_old_backups()
    return backup_path

def clean_old_backups():
    """Supprime les backups de plus de 30 jours"""
    if not os.path.exists(BACKUP_DIR):
        return
    cutoff = datetime.now() - timedelta(days=30)

    for filename in os.listdir(BACKUP_DIR):
        if filename.startswith('flotte_backup_') and filename.endswith('.db'):
            filepath = os.path.join(BACKUP_DIR, filename)
            file_time = datetime.fromtimestamp(os.path.getctime(filepath))
            if file_time < cutoff:
                os.remove(filepath)
                print(f"🗑️ Ancien backup supprimé : {filename}")

def list_backups():
    """Liste tous les backups disponibles"""
    if not os.path.exists(BACKUP_DIR):
        return []
    backups = []
    for filename in os.listdir(BACKUP_DIR):
        if filename.startswith('flotte_backup_') and filename.endswith('.db'):
            filepath = os.path.join(BACKUP_DIR, filename)
            size = os.path.getsize(filepath) / 1024  # KB
            date = datetime.fromtimestamp(os.path.getctime(filepath))
            backups.append({
                'filename': filename,
                'path': filepath,
                'size': f"{size:.1f} KB",
                'date': date.strftime('%d/%m/%Y %H:%M')
            })
    return sorted(backups, key=lambda x: x['date'], reverse=True)

def restore_backup(backup_path):
    """Restaure un backup"""
    if os.path.exists(backup_path):
        create_backup() # Backup actuel avant restauration
        shutil.copy2(backup_path, DB_FILE)
        return True
    return False

# ============================================
# GESTION DES PHOTOS
# ============================================
def save_vehicle_photo(source_path, vehicle_id):
    """Copie et redimensionne la photo du véhicule"""
    if not source_path or not os.path.exists(source_path):
        return None
    
    ext = os.path.splitext(source_path)[1].lower()
    if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.bmp']:
        messagebox.showerror("Erreur", "Format d'image non supporté. Utilisez JPG, PNG, GIF ou BMP.")
        return None

    photo_name = f"vehicule_{vehicle_id}{ext}"
    photo_path = os.path.join(PHOTOS_DIR, photo_name)

    try:
        img = Image.open(source_path)
        resample_method = getattr(Image, 'Resampling', Image).LANCZOS
        img.thumbnail((800, 600), resample_method)
        img.save(photo_path, quality=85)
        return photo_path
    except Exception as e:
        messagebox.showerror("Erreur", f"Impossible de traiter l'image : {str(e)}")
        return None

def get_photo_thumbnail(photo_path, size=(120, 90)):
    """Crée une miniature pour l'affichage dans les listes"""
    if not photo_path or not os.path.exists(photo_path):
        return None
    try:
        img = Image.open(photo_path)
        resample_method = getattr(Image, 'Resampling', Image).LANCZOS
        img.thumbnail(size, resample_method)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None

# ============================================
# CLASSE BASE DE DONNÉES
# ============================================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect(DB_FILE)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;") # Activation des clés étrangères

    def close(self):
        self.conn.close()

    # --- VÉHICULES ---
    def get_vehicules(self):
        return self.conn.execute("SELECT * FROM vehicules ORDER BY marque, modele").fetchall()

    def get_vehicule(self, id):
        return self.conn.execute("SELECT * FROM vehicules WHERE id = ?", (id,)).fetchone()

    def add_vehicule(self, data):
        c = self.conn.cursor()
        c.execute('''
            INSERT INTO vehicules (marque, modele, immatriculation, categorie, 
                                  prix_jour, date_assurance, date_visite, date_agrement, photo_path,
                                  statut, kilometrage)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (data['marque'], data['modele'], data['immat'], data['categorie'],
              data['prix'], data['assurance'], data['visite'], data['agrement'], data.get('photo_path'),
              data.get('statut', 'Disponible'), data.get('kilometrage')))
        self.conn.commit()
        return c.lastrowid 

    def update_vehicule(self, id, data):
        self.conn.execute('''
            UPDATE vehicules SET marque=?, modele=?, immatriculation=?, categorie=?,
                               prix_jour=?, date_assurance=?, date_visite=?, date_agrement=?,
                               photo_path=COALESCE(?, photo_path),
                               statut=COALESCE(?, statut), kilometrage=COALESCE(?, kilometrage)
            WHERE id=?
        ''', (data['marque'], data['modele'], data['immat'], data['categorie'],
              data['prix'], data['assurance'], data['visite'], data['agrement'], 
              data.get('photo_path'), data.get('statut'), data.get('kilometrage'), id))
        self.conn.commit()

    def set_vehicle_statut(self, vehicle_id, statut):
        self.conn.execute("UPDATE vehicules SET statut = ? WHERE id = ?", (statut, vehicle_id))
        self.conn.commit()

    def update_vehicle_photo(self, id, photo_path):
        self.conn.execute("UPDATE vehicules SET photo_path = ? WHERE id = ?", (photo_path, id))
        self.conn.commit()

    def delete_vehicule(self, id):
        veh = self.get_vehicule(id)
        if veh and veh['photo_path'] and os.path.exists(veh['photo_path']):
            try:
                os.remove(veh['photo_path'])
            except Exception:
                pass
        self.conn.execute("DELETE FROM vehicules WHERE id=?", (id,))
        self.conn.commit()

    # --- LOCATIONS ---
    def get_locations(self):
        return self.conn.execute('''
            SELECT l.*, v.marque, v.modele, v.immatriculation, v.photo_path,
                   CASE WHEN l.date_retour_reelle IS NOT NULL THEN 'Terminée'
                        WHEN date('now') BETWEEN l.date_debut AND l.date_fin THEN 'En cours'
                        WHEN date('now') < l.date_debut THEN 'À venir'
                        ELSE 'Retard' END AS statut_loc
            FROM locations l
            JOIN vehicules v ON l.vehicule_id = v.id
            ORDER BY l.date_debut DESC
        ''').fetchall()

    def get_location(self, id):
        return self.conn.execute("SELECT * FROM locations WHERE id = ?", (id,)).fetchone()

    def count_active_locations(self):
        """Locations en cours aujourd'hui (= véhicules loués)."""
        return self.conn.execute('''
            SELECT COUNT(*) FROM locations
            WHERE date_retour_reelle IS NULL AND date('now') BETWEEN date_debut AND date_fin
        ''').fetchone()[0]

    def has_date_conflict(self, vehicule_id, debut, fin, exclude_id=None):
        """Vérifie si le véhicule est déjà loué sur cette période."""
        q = '''
            SELECT COUNT(*) FROM locations
            WHERE vehicule_id = ? AND date_retour_reelle IS NULL
              AND date_debut <= ? AND date_fin >= ?
        '''
        params = [vehicule_id, fin, debut]
        if exclude_id:
            q += " AND id != ?"
            params.append(exclude_id)
        return self.conn.execute(q, params).fetchone()[0] > 0

    def add_location(self, data):
        c = self.conn.cursor()
        c.execute('''
            INSERT INTO locations (date_location, vehicule_id, client, date_debut, 
                                 date_fin, jours, prix_jour, total, paiement, acompte, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (data['date'], data['vehicule_id'], data['client'], data['debut'],
              data['fin'], data['jours'], data['prix'], data['total'], data['paiement'],
              data.get('acompte', 0), data.get('notes')))
        # Marquer le véhicule comme loué si la location couvre aujourd'hui
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            if data['debut'] <= today <= data['fin']:
                self.set_vehicle_statut(data['vehicule_id'], 'Loué')
        except Exception:
            pass
        self.conn.commit()
        return c.lastrowid

    def cloturer_location(self, loc_id):
        """Enregistre le retour réel du véhicule et rend le véhicule disponible."""
        loc = self.get_location(loc_id)
        if not loc:
            return False
        self.conn.execute(
            "UPDATE locations SET date_retour_reelle = date('now') WHERE id = ?", (loc_id,))
        self.conn.execute(
            "UPDATE vehicules SET statut = 'Disponible' WHERE id = ?", (loc['vehicule_id'],))
        self.conn.commit()
        return True

    def delete_location(self, id):
        self.conn.execute("DELETE FROM locations WHERE id=?", (id,))
        self.conn.commit()

    def get_total_locations(self):
        result = self.conn.execute("SELECT COALESCE(SUM(total), 0) FROM locations").fetchone()
        return result[0]

    def get_monthly_stats(self):
        """Revenus/charges des 6 derniers mois (ordre chronologique)."""
        rows = self.conn.execute(
            "SELECT mois, revenus, charges, resultat FROM stats_mensuelles ORDER BY mois ASC"
        ).fetchall()
        return rows

    # --- CHARGES ---
    def get_charges_fixes(self):
        return self.conn.execute('''
            SELECT c.*, v.marque, v.modele 
            FROM charges_fixes c
            LEFT JOIN vehicules v ON c.vehicule_id = v.id
            ORDER BY c.date_charge DESC
        ''').fetchall()

    def add_charge_fixe(self, data):
        c = self.conn.cursor()
        c.execute('''
            INSERT INTO charges_fixes (date_charge, vehicule_id, type, montant, periode, mois)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (data['date'], data.get('vehicule_id'), data['type'], 
              data['montant'], data.get('periode'), data.get('mois')))
        self.conn.commit()
        return c.lastrowid

    def get_charges_variables(self):
        return self.conn.execute('''
            SELECT c.*, v.marque, v.modele 
            FROM charges_variables c
            LEFT JOIN vehicules v ON c.vehicule_id = v.id
            ORDER BY c.date_charge DESC
        ''').fetchall()

    def add_charge_variable(self, data):
        c = self.conn.cursor()
        c.execute('''
            INSERT INTO charges_variables (date_charge, vehicule_id, type, montant, description)
            VALUES (?, ?, ?, ?, ?)
        ''', (data['date'], data.get('vehicule_id'), data['type'], 
              data['montant'], data.get('description')))
        self.conn.commit()
        return c.lastrowid

    def get_total_charges(self):
        fixe = self.conn.execute("SELECT COALESCE(SUM(montant), 0) FROM charges_fixes").fetchone()[0]
        var = self.conn.execute("SELECT COALESCE(SUM(montant), 0) FROM charges_variables").fetchone()[0]
        pers = self.conn.execute("SELECT COALESCE(SUM(montant), 0) FROM personnel").fetchone()[0]
        return fixe + var + pers, fixe, var, pers

    # --- PERSONNEL ---
    def get_personnel(self):
        return self.conn.execute("SELECT * FROM personnel ORDER BY date_paie DESC").fetchall()

    def add_personnel(self, data):
        c = self.conn.cursor()
        c.execute('''
            INSERT INTO personnel (date_paie, montant, description, nom, poste)
            VALUES (?, ?, ?, ?, ?)
        ''', (data['date'], data['montant'], data.get('description'),
              data.get('nom'), data.get('poste')))
        self.conn.commit()
        return c.lastrowid

    def delete_personnel(self, id):
        self.conn.execute("DELETE FROM personnel WHERE id=?", (id,))
        self.conn.commit()

    # --- ALERTES ---
    def get_alertes(self):
        vehicules = self.get_vehicules()
        alertes = []
        today = datetime.now().date()
        
        for v in vehicules:
            docs = [
                ('Assurance', v['date_assurance']),
                ('Visite Technique', v['date_visite']),
                ('Agrément', v['date_agrement'])
            ]
            for doc_name, date_str in docs:
                if not date_str:
                    continue
                try:
                    exp = datetime.strptime(date_str, '%Y-%m-%d').date()
                    jours = (exp - today).days
                    
                    if jours < 0:
                        statut = 'EXPIRÉ'
                    elif jours <= 30:
                        statut = 'À RENOUVELER'
                    else:
                        continue
                    
                    alertes.append({
                        'voiture': f"{v['marque']} {v['modele']}",
                        'immat': v['immatriculation'],
                        'document': doc_name,
                        'expiration': date_str,
                        'jours': jours,
                        'statut': statut,
                        'photo_path': v['photo_path']
                    })
                except Exception:
                    continue
        
        return sorted(alertes, key=lambda x: x['jours'])

# ============================================
# INTERFACE GRAPHIQUE
# ============================================
class Application:
    def __init__(self, root):
        self.root = root
        self.root.title("🚗 Gestion Agence de Location - Parc Auto")
        self.root.geometry("1500x950")
        self.root.configure(bg='#f4f6f9')
        self.db = Database()
        self.photo_cache = {}  # Cache pour les miniatures
        
        self.colors = {
            'primary': '#1e3a5f',
            'secondary': '#0d8b6d',
            'danger': '#dc3545',
            'warning': '#f39c12',
            'bg': '#f4f6f9',
            'card': '#ffffff'
        }
        
        self.setup_ui()
        self.show_dashboard()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def on_closing(self):
        """Backup automatique avant fermeture"""
        backup_path = create_backup()
        if backup_path:
            print(f"💾 Backup créé : {backup_path}")
        self.db.close()
        self.root.destroy()

    def setup_ui(self):
        sidebar = Frame(self.root, bg=self.colors['primary'], width=260)
        sidebar.pack(side=LEFT, fill=Y)
        sidebar.pack_propagate(False)
        
        Label(sidebar, text="🚗 GESTION\nLOCATION", 
              bg=self.colors['primary'], fg='white',
              font=('Segoe UI', 16, 'bold'), pady=20).pack()
        
        Label(sidebar, text="Application de gestion\nde parc automobile", 
              bg=self.colors['primary'], fg='#a0b2c6',
              font=('Segoe UI', 9), justify=CENTER).pack(pady=(0, 20))
        
        buttons = [
            ("📊 Tableau de Bord", self.show_dashboard),
            ("🚙 Parc Auto", self.show_vehicules),
            ("🤝 Locations", self.show_locations),
            ("💰 Charges", self.show_charges),
            ("👥 Personnel", self.show_personnel),
            ("⚠️ Alertes", self.show_alertes),
            ("💾 Backups", self.show_backups),
        ]
        
        for text, command in buttons:
            btn = Button(sidebar, text=text, command=command,
                        bg=self.colors['primary'], fg='white',
                        font=('Segoe UI', 11), bd=0, pady=14, padx=20,
                        activebackground='#2a5285', cursor='hand2',
                        anchor='w', width=28)
            btn.pack(fill=X)
        
        Frame(sidebar, bg='white', height=1).pack(fill=X, padx=20, pady=20)
        Label(sidebar, text="💡 Backup auto à la fermeture\n📁 Dossier : backups/", 
              bg=self.colors['primary'], fg='#8fa4bc',
              font=('Segoe UI', 8), justify=CENTER).pack(pady=10)
        
        self.main_frame = Frame(self.root, bg=self.colors['bg'])
        self.main_frame.pack(side=LEFT, fill=BOTH, expand=True, padx=25, pady=25)

    def clear_main(self):
        for widget in self.main_frame.winfo_children():
            widget.destroy()

    def create_kpi_card(self, parent, title, value, color, icon=""):
        card = Frame(parent, bg='white', bd=0, relief='solid',
                    highlightbackground='#e1e8ed', highlightthickness=1)
        card.pack(side=LEFT, fill=BOTH, expand=True, padx=8, pady=8)
        
        Frame(card, bg=color, height=4).pack(fill=X)
        
        inner = Frame(card, bg='white')
        inner.pack(fill=BOTH, expand=True, padx=20, pady=20)
        
        Label(inner, text=f"{icon} {title}", bg='white', fg='#7f8c8d',
              font=('Segoe UI', 10)).pack(anchor='w')
        Label(inner, text=value, bg='white', fg=color,
              font=('Segoe UI', 26, 'bold')).pack(anchor='w', pady=(10, 0))
        
        return card

    # ============================================
    # TABLEAU DE BORD
    # ============================================
    def show_dashboard(self):
        self.clear_main()
        
        header = Frame(self.main_frame, bg=self.colors['bg'])
        header.pack(fill=X, pady=(0, 20))
        
        Label(header, text="Tableau de Bord", 
              bg=self.colors['bg'], fg=self.colors['primary'],
              font=('Segoe UI', 28, 'bold')).pack(side=LEFT)
        
        Label(header, text=datetime.now().strftime('%d/%m/%Y'), 
              bg=self.colors['bg'], fg='#7f8c8d',
              font=('Segoe UI', 12)).pack(side=RIGHT, pady=10)
        
        kpi_frame = Frame(self.main_frame, bg=self.colors['bg'])
        kpi_frame.pack(fill=X, pady=10)
        
        nb_veh = len(self.db.get_vehicules())
        nb_loc_en_cours = self.db.count_active_locations()
        total_loc = self.db.get_total_locations() or 0
        total_charges, _, _, _ = self.db.get_total_charges()
        result = total_loc - total_charges
        alertes = len(self.db.get_alertes())
        
        self.create_kpi_card(kpi_frame, "VÉHICULES", str(nb_veh), '#3498db', '🚗')
        self.create_kpi_card(kpi_frame, "LOCATIONS EN COURS", str(nb_loc_en_cours), '#0d8b6d', '🤝')
        self.create_kpi_card(kpi_frame, "REVENUS", f"{total_loc:,.0f} DH", '#9b59b6', '💰')
        self.create_kpi_card(kpi_frame, "CHARGES", f"{total_charges:,.0f} DH", '#dc3545', '📉')
        self.create_kpi_card(kpi_frame, "RÉSULTAT", f"{result:,.0f} DH", 
                            '#0d8b6d' if result >= 0 else '#dc3545', '💵')
        self.create_kpi_card(kpi_frame, "ALERTES", str(alertes), '#f39c12', '⚠️')

        # --- Graphique Revenus vs Charges (6 derniers mois) ---
        stats = self.db.get_monthly_stats()
        if stats:
            chart_card = Frame(self.main_frame, bg='white', bd=1, relief='solid',
                               highlightbackground='#e1e8ed', highlightthickness=1)
            chart_card.pack(fill=X, pady=(5, 10), ipady=8)
            Frame(chart_card, bg='#9b59b6', height=4).pack(fill=X)
            Label(chart_card, text="📈 Revenus vs Charges — 6 derniers mois",
                  bg='white', fg=self.colors['primary'],
                  font=('Segoe UI', 13, 'bold')).pack(anchor='w', padx=20, pady=(12, 4))

            canvas = Canvas(chart_card, bg='white', height=170, highlightthickness=0)
            canvas.pack(fill=X, padx=20, pady=(0, 12))

            def draw_chart(event):
                canvas.delete('all')
                w, h = event.width, 160
                n = len(stats)
                max_val = max([max(s['revenus'] or 0, s['charges'] or 0) for s in stats] + [1])
                group_w = w / max(n, 1)
                bar_w = min(group_w * 0.28, 46)
                months_fr = ['jan', 'fév', 'mar', 'avr', 'mai', 'jun',
                             'jul', 'aoû', 'sep', 'oct', 'nov', 'déc']
                for i, s in enumerate(stats):
                    cx = group_w * (i + 0.5)
                    rh = (s['revenus'] or 0) / max_val * (h - 40)
                    ch = (s['charges'] or 0) / max_val * (h - 40)
                    canvas.create_rectangle(cx - bar_w - 3, h - rh, cx - 3, h,
                                            fill='#0d8b6d', width=0)
                    canvas.create_rectangle(cx + 3, h - ch, cx + bar_w + 3, h,
                                            fill='#dc3545', width=0)
                    try:
                        y, m = s['mois'].split('-')
                        lbl = f"{months_fr[int(m)-1]} {y[2:]}"
                    except Exception:
                        lbl = s['mois']
                    canvas.create_text(cx, h + 0, text=lbl, font=('Segoe UI', 9),
                                       fill='#7f8c8d', anchor='n')
                    canvas.create_text(cx - bar_w/2 - 3, h - rh - 2,
                                       text=f"{int(s['revenus'] or 0):,} DH".replace(',', ' '),
                                       font=('Segoe UI', 8), fill='#0d8b6d', anchor='s')
                canvas.create_line(0, h, w, h, fill='#e1e8ed')
            canvas.bind('<Configure>', draw_chart)

            legend = Frame(chart_card, bg='white')
            legend.pack(anchor='w', padx=20, pady=(0, 10))
            Label(legend, text="■ Revenus", bg='white', fg='#0d8b6d',
                  font=('Segoe UI', 9, 'bold')).pack(side=LEFT, padx=(0, 15))
            Label(legend, text="■ Charges", bg='white', fg='#dc3545',
                  font=('Segoe UI', 9, 'bold')).pack(side=LEFT)
        
        alert_frame = Frame(self.main_frame, bg='white', bd=1, relief='solid',
                           highlightbackground='#e1e8ed', highlightthickness=1)
        alert_frame.pack(fill=X, pady=20, ipady=10)
        
        Frame(alert_frame, bg='#f39c12', height=4).pack(fill=X)
        
        header_alert = Frame(alert_frame, bg='white')
        header_alert.pack(fill=X, padx=20, pady=15)
        
        Label(header_alert, text="⚠️ Alertes Prioritaires", 
              bg='white', fg=self.colors['primary'],
              font=('Segoe UI', 16, 'bold')).pack(side=LEFT)

        Button(header_alert, text="Voir toutes les alertes →", command=self.show_alertes,
               bg='white', fg=self.colors['primary'], font=('Segoe UI', 9, 'bold'),
               bd=0, cursor='hand2').pack(side=RIGHT)
        
        alertes_list = self.db.get_alertes()[:5]
        if not alertes_list:
            Label(alert_frame, text="✅ Tous les documents sont à jour", 
                  bg='white', fg='#0d8b6d', font=('Segoe UI', 12)).pack(pady=20)
        else:
            for a in alertes_list:
                color = '#dc3545' if a['statut'] == 'EXPIRÉ' else '#f39c12'
                bg_color = '#fdeaea' if a['statut'] == 'EXPIRÉ' else '#fef5e7'
                
                row = Frame(alert_frame, bg=bg_color)
                row.pack(fill=X, padx=20, pady=4, ipady=8)
                
                if a['photo_path'] and os.path.exists(a['photo_path']):
                    thumb = get_photo_thumbnail(a['photo_path'], (50, 40))
                    if thumb:
                        lbl = Label(row, image=thumb, bg=bg_color)
                        lbl.image = thumb
                        lbl.pack(side=LEFT, padx=(10, 5))
                
                Label(row, text=f"🚗 {a['voiture']} ({a['immat']})", bg=bg_color,
                      font=('Segoe UI', 11, 'bold')).pack(side=LEFT, padx=10)
                Label(row, text=a['document'], bg=bg_color,
                      font=('Segoe UI', 10)).pack(side=LEFT, padx=10)
                Label(row, text=a['expiration'], bg=bg_color, fg=color,
                      font=('Segoe UI', 10, 'bold')).pack(side=LEFT, padx=10)
                Label(row, text=a['statut'], bg=color, fg='white',
                      font=('Segoe UI', 9, 'bold')).pack(side=RIGHT, padx=20, ipadx=10, ipady=4)

    # ============================================
    # PARC AUTO
    # ============================================
    def show_vehicules(self):
        self.clear_main()
        
        header = Frame(self.main_frame, bg=self.colors['bg'])
        header.pack(fill=X, pady=(0, 20))
        
        Label(header, text="🚙 Parc Automobile", 
              bg=self.colors['bg'], fg=self.colors['primary'],
              font=('Segoe UI', 28, 'bold')).pack(side=LEFT)
        
        Button(header, text="+ Ajouter un véhicule", command=self.add_vehicule_dialog,
               bg=self.colors['secondary'], fg='white', font=('Segoe UI', 11),
               bd=0, padx=25, pady=10, cursor='hand2',
               activebackground='#0a7a5e').pack(side=RIGHT)
        
        filter_frame = Frame(self.main_frame, bg=self.colors['bg'])
        filter_frame.pack(fill=X, pady=(0, 15))
        
        Label(filter_frame, text="🔍 Rechercher : ", bg=self.colors['bg'],
              font=('Segoe UI', 10)).pack(side=LEFT)
        
        self.veh_search = Entry(filter_frame, font=('Segoe UI', 11), width=30)
        self.veh_search.pack(side=LEFT, padx=10)
        self.veh_search.bind('<KeyRelease>', lambda e: self.refresh_vehicles_list())
        
        Label(filter_frame, text="Catégorie : ", bg=self.colors['bg'],
              font=('Segoe UI', 10)).pack(side=LEFT, padx=(20, 0))
        
        self.veh_filter_cat = ttk.Combobox(filter_frame, 
                                           values=['Toutes', 'Economique', 'Compacte', 'Premium'],
                                          width=15, state='readonly')
        self.veh_filter_cat.set('Toutes')
        self.veh_filter_cat.pack(side=LEFT, padx=10)
        self.veh_filter_cat.bind('<<ComboboxSelected>>', lambda e: self.refresh_vehicles_list())

        Label(filter_frame, text="Statut : ", bg=self.colors['bg'],
              font=('Segoe UI', 10)).pack(side=LEFT, padx=(20, 0))

        self.veh_filter_statut = ttk.Combobox(filter_frame,
                                              values=['Tous', 'Disponible', 'Loué', 'Maintenance', 'Vendu'],
                                              width=13, state='readonly')
        self.veh_filter_statut.set('Tous')
        self.veh_filter_statut.pack(side=LEFT, padx=10)
        self.veh_filter_statut.bind('<<ComboboxSelected>>', lambda e: self.refresh_vehicles_list())
        
        canvas = Canvas(self.main_frame, bg=self.colors['bg'], highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.main_frame, orient=VERTICAL, command=canvas.yview)
        self.vehicles_container = Frame(canvas, bg=self.colors['bg'])
        
        self.vehicles_container.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=self.vehicles_container, anchor="nw", width=1200)
        canvas.configure(yscrollcommand=scrollbar.set)
        
        scrollbar.pack(side=RIGHT, fill=Y)
        canvas.pack(side=LEFT, fill=BOTH, expand=True)
        
        def on_mousewheel(event):
            if event.num == 4 or event.delta > 0:
                canvas.yview_scroll(-1, "units")
            elif event.num == 5 or event.delta < 0:
                canvas.yview_scroll(1, "units")
        
        canvas.bind("<Enter>", lambda e: [
            canvas.bind_all("<MouseWheel>", on_mousewheel),
            canvas.bind_all("<Button-4>", on_mousewheel),
            canvas.bind_all("<Button-5>", on_mousewheel)
        ])
        canvas.bind("<Leave>", lambda e: [
            canvas.unbind_all("<MouseWheel>"),
            canvas.unbind_all("<Button-4>"),
            canvas.unbind_all("<Button-5>")
        ])
        
        self.refresh_vehicles_list()

    def refresh_vehicles_list(self):
        for widget in self.vehicles_container.winfo_children():
            widget.destroy()
        
        vehicules = self.db.get_vehicules()
        search = self.veh_search.get().lower()
        cat = self.veh_filter_cat.get()
        statut = self.veh_filter_statut.get()
        
        filtered = []
        for v in vehicules:
            text = f"{v['marque']} {v['modele']} {v['immatriculation']}".lower()
            if search and search not in text:
                continue
            if cat != 'Toutes' and v['categorie'] != cat:
                continue
            if statut != 'Tous':
                vs = (v['statut'] if 'statut' in v.keys() else None) or 'Disponible'
                if vs != statut:
                    continue
            filtered.append(v)
        
        if not filtered:
            Label(self.vehicles_container, text="Aucun véhicule trouvé", 
                  bg=self.colors['bg'], fg='#7f8c8d',
                  font=('Segoe UI', 14)).pack(pady=50)
            return
        
        row_frame = None
        for i, v in enumerate(filtered):
            if i % 3 == 0:
                row_frame = Frame(self.vehicles_container, bg=self.colors['bg'])
                row_frame.pack(fill=X, pady=10)
            
            self.create_vehicle_card(row_frame, v)

    def create_vehicle_card(self, parent, v):
        card = Frame(parent, bg='white', bd=0, relief='solid',
                    highlightbackground='#e1e8ed', highlightthickness=1,
                    width=380, height=320)
        card.pack(side=LEFT, fill=BOTH, expand=True, padx=10)
        card.pack_propagate(False)
        
        photo_frame = Frame(card, bg='#f4f6f9', height=160)
        photo_frame.pack(fill=X)
        photo_frame.pack_propagate(False)
        
        if v['photo_path'] and os.path.exists(v['photo_path']):
            try:
                if v['id'] in self.photo_cache:
                    photo = self.photo_cache[v['id']]
                else:
                    img = Image.open(v['photo_path'])
                    resample_method = getattr(Image, 'Resampling', Image).LANCZOS
                    img.thumbnail((380, 160), resample_method)
                    photo = ImageTk.PhotoImage(img)
                    self.photo_cache[v['id']] = photo

                lbl = Label(photo_frame, image=photo, bg='#f4f6f9')
                lbl.image = photo
                lbl.pack(fill=BOTH, expand=True)
            except Exception:
                Label(photo_frame, text="🚗\nPas de photo", 
                      bg='#f4f6f9', fg='#bdc3c7',
                      font=('Segoe UI', 30)).pack(expand=True)
        else:
            Label(photo_frame, text="🚗\nPas de photo", 
                  bg='#f4f6f9', fg='#bdc3c7',
                  font=('Segoe UI', 30)).pack(expand=True)
        
        if not v['photo_path']:
            btn_photo = Button(photo_frame, text="📷 Ajouter photo", 
                              command=lambda: self.add_photo_to_vehicle(v['id']),
                              bg='#333333', fg='white',
                              font=('Segoe UI', 9), bd=0, padx=10, pady=4,
                              cursor='hand2')
            btn_photo.place(relx=0.5, rely=0.5, anchor=CENTER)
        
        info = Frame(card, bg='white', padx=15, pady=12)
        info.pack(fill=BOTH, expand=True)
        
        header = Frame(info, bg='white')
        header.pack(fill=X)
        
        Label(header, text=f"{v['marque']} {v['modele']}", 
              bg='white', fg=self.colors['primary'],
              font=('Segoe UI', 14, 'bold')).pack(side=LEFT)
        
        cat_colors = {'Economique': '#0d8b6d', 'Compacte': '#3498db', 'Premium': '#f39c12'}
        Label(header, text=v['categorie'], 
              bg=cat_colors.get(v['categorie'], '#7f8c8d'), fg='white',
              font=('Segoe UI', 9, 'bold'), padx=10, pady=2).pack(side=RIGHT)
        
        Label(info, text=f"🆔 {v['immatriculation']}", 
              bg='white', fg='#7f8c8d',
              font=('Segoe UI', 11)).pack(anchor='w', pady=(8, 0))

        # Badge de statut + kilométrage
        try:
            v_statut = (v['statut'] if 'statut' in v.keys() else None) or 'Disponible'
        except Exception:
            v_statut = 'Disponible'
        statut_colors = {'Disponible': '#0d8b6d', 'Loué': '#3498db',
                         'Maintenance': '#f39c12', 'Vendu': '#7f8c8d'}
        Label(info, text=f"● {v_statut}",
              bg='white', fg=statut_colors.get(v_statut, '#7f8c8d'),
              font=('Segoe UI', 10, 'bold')).pack(anchor='w', pady=(2, 0))

        try:
            km = v['kilometrage'] if 'kilometrage' in v.keys() else None
        except Exception:
            km = None
        if km:
            Label(info, text=f"🛣️ {km:,.0f} km".replace(',', ' '),
                  bg='white', fg='#7f8c8d',
                  font=('Segoe UI', 9)).pack(anchor='w')
        
        Label(info, text=f"💰 {v['prix_jour']:,.0f} DH/jour", 
              bg='white', fg=self.colors['secondary'],
              font=('Segoe UI', 12, 'bold')).pack(anchor='w', pady=(5, 0))
        
        today = datetime.now().date()
        dates_frame = Frame(info, bg='white')
        dates_frame.pack(fill=X, pady=(10, 0))
        
        for label, date_str, color_default in [
            ('Assurance', v['date_assurance'], '#0d8b6d'),
            ('Visite', v['date_visite'], '#0d8b6d'),
            ('Agrément', v['date_agrement'], '#0d8b6d')
        ]:
            if date_str:
                try:
                    exp = datetime.strptime(date_str, '%Y-%m-%d').date()
                    jours = (exp - today).days
                    if jours < 0:
                        color = '#dc3545'
                        status = "EXPIRÉ"
                    elif jours <= 30:
                        color = '#f39c12'
                        status = f"{jours}j"
                    else:
                        color = color_default
                        status = f"{jours}j"
                except Exception:
                    color = '#7f8c8d'
                    status = "?"
            else:
                color = '#7f8c8d'
                status = "N/A"
            
            lbl = Label(dates_frame, text=f"{label}: {status}", 
                       bg='white', fg=color,
                       font=('Segoe UI', 9))
            lbl.pack(side=LEFT, padx=(0, 15))
        
        actions = Frame(info, bg='white')
        actions.pack(fill=X, pady=(10, 0))
        
        Button(actions, text="✏️ Modifier", 
               command=lambda: self.edit_vehicule_dialog(v['id']),
               bg=self.colors['primary'], fg='white',
               font=('Segoe UI', 9), bd=0, padx=15, pady=6,
               cursor='hand2', activebackground='#2a5285').pack(side=LEFT, padx=(0, 8))
        
        Button(actions, text="🗑️ Supprimer", 
               command=lambda: self.delete_vehicule(v['id']),
               bg='#fdeaea', fg='#dc3545',
               font=('Segoe UI', 9), bd=0, padx=15, pady=6,
               cursor='hand2').pack(side=LEFT)
        
        if v['photo_path']:
            Button(actions, text="📷 Changer", 
                   command=lambda: self.add_photo_to_vehicle(v['id']),
                   bg='#f4f6f9', fg=self.colors['primary'],
                   font=('Segoe UI', 9), bd=0, padx=10, pady=6,
                   cursor='hand2').pack(side=RIGHT)

    def add_photo_to_vehicle(self, vehicle_id):
        file_path = filedialog.askopenfilename(
            title="Choisir une photo du véhicule",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.gif *.bmp"), ("Tous fichiers", "*.*")]
        )
        if file_path:
            photo_path = save_vehicle_photo(file_path, vehicle_id)
            if photo_path:
                self.db.update_vehicle_photo(vehicle_id, photo_path)
                if vehicle_id in self.photo_cache:
                    del self.photo_cache[vehicle_id]
                messagebox.showinfo("Succès", "Photo ajoutée avec succès !")
                self.refresh_vehicles_list()

    def add_vehicule_dialog(self):
        dialog = Toplevel(self.root)
        dialog.title("Ajouter un véhicule")
        dialog.geometry("550x750")
        dialog.transient(self.root)
        dialog.grab_set()
        
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (550 // 2)
        y = (dialog.winfo_screenheight() // 2) - (750 // 2)
        dialog.geometry(f"+{x}+{y}")
        
        Label(dialog, text="🚗 Nouveau Véhicule", 
              font=('Segoe UI', 18, 'bold'), fg=self.colors['primary']).pack(pady=20)
        
        # --- Utilisation de ScrollableFrame ---
        scroll_frame = ScrollableFrame(dialog, bg='#ffffff')
        scroll_frame.pack(fill=BOTH, expand=True, padx=30, pady=(0, 20))
        form = scroll_frame.scrollable_frame
        
        self.temp_photo_path = None
        photo_preview = Frame(form, bg='#f4f6f9', height=150, width=300)
        photo_preview.pack(pady=10)
        photo_preview.pack_propagate(False)
        
        self.photo_lbl = Label(photo_preview, text="📷\nAucune photo", 
                               bg='#f4f6f9', fg='#bdc3c7',
                               font=('Segoe UI', 14))
        self.photo_lbl.pack(expand=True)
        
        Button(form, text="📷 Choisir une photo", 
               command=lambda: self.choose_temp_photo(photo_preview),
               bg=self.colors['primary'], fg='white',
               font=('Segoe UI', 10), bd=0, padx=20, pady=8,
               cursor='hand2').pack(pady=10)
        
        fields = [
            ('Marque *', 'marque'),
            ('Modèle *', 'modele'),
            ('Immatriculation *', 'immat'),
            ('Prix/Jour (DH) *', 'prix'),
        ]
        
        self.entries = {}
        for label, key in fields:
            Label(form, text=label, font=('Segoe UI', 11), anchor='w').pack(fill=X, pady=(15, 5))
            entry = Entry(form, font=('Segoe UI', 12), relief='solid', bd=1)
            entry.pack(fill=X, ipady=6)
            self.entries[key] = entry
        
        Label(form, text="Catégorie", font=('Segoe UI', 11), anchor='w').pack(fill=X, pady=(15, 5))
        self.cat_combo = ttk.Combobox(form, values=['Economique', 'Compacte', 'Premium'],
                                       font=('Segoe UI', 11), state='readonly')
        self.cat_combo.set('Economique')
        self.cat_combo.pack(fill=X, ipady=4)

        # Statut + kilométrage
        statut_km_frame = Frame(form)
        statut_km_frame.pack(fill=X, pady=(10, 0))

        col_s = Frame(statut_km_frame)
        col_s.pack(side=LEFT, fill=X, expand=True)
        Label(col_s, text="Statut", font=('Segoe UI', 11), anchor='w').pack(anchor='w', pady=(0, 5))
        self.statut_combo = ttk.Combobox(col_s, values=['Disponible', 'Loué', 'Maintenance', 'Vendu'],
                                         font=('Segoe UI', 11), state='readonly')
        self.statut_combo.set('Disponible')
        self.statut_combo.pack(fill=X, ipady=4)

        col_k = Frame(statut_km_frame)
        col_k.pack(side=LEFT, fill=X, expand=True, padx=(15, 0))
        Label(col_k, text="Kilométrage", font=('Segoe UI', 11), anchor='w').pack(anchor='w', pady=(0, 5))
        self.km_entry = Entry(col_k, font=('Segoe UI', 12), relief='solid', bd=1)
        self.km_entry.pack(fill=X, ipady=6)
        
        dates_frame = Frame(form)
        dates_frame.pack(fill=X, pady=15)
        
        self.date_entries = {}
        for i, (label, key) in enumerate([
            ('Assurance', 'assurance'),
            ('Visite Tech.', 'visite'),
            ('Agrément', 'agrement')
        ]):
            col = Frame(dates_frame)
            col.pack(side=LEFT, fill=X, expand=True, padx=(0 if i==0 else 10, 0))
            
            Label(col, text=label, font=('Segoe UI', 10)).pack(anchor='w')
            entry = Entry(col, font=('Segoe UI', 10), relief='solid', bd=1)
            entry.insert(0, datetime.now().strftime('%Y-%m-%d'))
            entry.pack(fill=X, ipady=4)
            self.date_entries[key] = entry
        
        btn_frame = Frame(dialog, pady=20)
        btn_frame.pack(fill=X)
        
        Button(btn_frame, text="❌ Annuler", command=dialog.destroy,
               bg='#f4f6f9', fg='#7f8c8d',
               font=('Segoe UI', 11, 'bold'), bd=0, padx=30, pady=12,
               cursor='hand2').pack(side=LEFT, padx=30)
        
        Button(btn_frame, text="✅ Enregistrer", 
               command=lambda: self.save_new_vehicule(dialog),
               bg=self.colors['secondary'], fg='white',
               font=('Segoe UI', 11, 'bold'), bd=0, padx=30, pady=12, 
               cursor='hand2', activebackground='#0a7a5e').pack(side=RIGHT, padx=30)

    def choose_temp_photo(self, preview_frame):
        file_path = filedialog.askopenfilename(
            title="Choisir une photo",
            filetypes=[("Images", "*.jpg *.jpeg *.png *.gif *.bmp")]
        )
        if file_path:
            self.temp_photo_path = file_path
            for w in preview_frame.winfo_children():
                w.destroy()
            try:
                img = Image.open(file_path)
                resample_method = getattr(Image, 'Resampling', Image).LANCZOS
                img.thumbnail((280, 140), resample_method)
                photo = ImageTk.PhotoImage(img)
                lbl = Label(preview_frame, image=photo, bg='#f4f6f9')
                lbl.image = photo
                lbl.pack(expand=True)
            except Exception:
                pass

    def _read_km_from_form(self):
        """Retourne le kilométrage saisi (float ou None) — lève ValueError si invalide."""
        val = self.km_entry.get().strip().replace(' ', '').replace(',', '.')
        if not val:
            return None
        km = float(val)
        if km < 0:
            raise ValueError("Le kilométrage ne peut pas être négatif.")
        return km

    def save_new_vehicule(self, dialog):
        try:
            prix_val = self.entries['prix'].get().strip().replace(',', '.')
            prix_jour = float(prix_val) if prix_val else 0.0
        except ValueError:
            messagebox.showerror("Erreur de saisie", "Le prix journalier doit être un nombre valide.")
            return

        try:
            kilometrage = self._read_km_from_form()
        except ValueError:
            messagebox.showerror("Erreur de saisie", "Le kilométrage doit être un nombre valide.")
            return

        try:
            data = {
                'marque': self.entries['marque'].get().strip(),
                'modele': self.entries['modele'].get().strip(),
                'immat': self.entries['immat'].get().strip(),
                'categorie': self.cat_combo.get(),
                'prix': prix_jour,
                'assurance': self.date_entries['assurance'].get() or None,
                'visite': self.date_entries['visite'].get() or None,
                'agrement': self.date_entries['agrement'].get() or None,
                'photo_path': None,
                'statut': self.statut_combo.get(),
                'kilometrage': kilometrage
            }
            
            if not all([data['marque'], data['modele'], data['immat']]):
                messagebox.showwarning("Attention", "Veuillez remplir les champs obligatoires (*)")
                return
            
            vehicle_id = self.db.add_vehicule(data)
            
            if self.temp_photo_path:
                photo_path = save_vehicle_photo(self.temp_photo_path, vehicle_id)
                if photo_path:
                    self.db.update_vehicle_photo(vehicle_id, photo_path)
            
            messagebox.showinfo("Succès", "Véhicule ajouté avec succès !")
            dialog.destroy()
            self.refresh_vehicles_list()
            
        except sqlite3.IntegrityError:
            messagebox.showerror("Doublon", "Un véhicule avec cette plaque d'immatriculation existe déjà dans le parc.")
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible d'ajouter : {str(e)}")

    def edit_vehicule_dialog(self, veh_id):
        v = self.db.get_vehicule(veh_id)
        if not v:
            return
        
        dialog = Toplevel(self.root)
        dialog.title(f"Modifier - {v['marque']} {v['modele']}")
        dialog.geometry("550x750")
        dialog.transient(self.root)
        dialog.grab_set()
        
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() // 2) - (550 // 2)
        y = (dialog.winfo_screenheight() // 2) - (750 // 2)
        dialog.geometry(f"+{x}+{y}")
        
        Label(dialog, text="✏️ Modifier Véhicule", 
              font=('Segoe UI', 18, 'bold'), fg=self.colors['primary']).pack(pady=20)
        
        # --- Utilisation de ScrollableFrame ---
        scroll_frame = ScrollableFrame(dialog, bg='#ffffff')
        scroll_frame.pack(fill=BOTH, expand=True, padx=30, pady=(0, 20))
        form = scroll_frame.scrollable_frame
        
        self.temp_photo_path = None
        photo_preview = Frame(form, bg='#f4f6f9', height=150, width=300)
        photo_preview.pack(pady=10)
        photo_preview.pack_propagate(False)
        
        self.photo_lbl = Label(photo_preview, text="📷\nAucune photo", 
                               bg='#f4f6f9', fg='#bdc3c7', font=('Segoe UI', 14))
        self.photo_lbl.pack(expand=True)
        
        if v['photo_path'] and os.path.exists(v['photo_path']):
            try:
                img = Image.open(v['photo_path'])
                resample_method = getattr(Image, 'Resampling', Image).LANCZOS
                img.thumbnail((280, 140), resample_method)
                photo = ImageTk.PhotoImage(img)
                self.photo_lbl.configure(image=photo, text="")
                self.photo_lbl.image = photo
            except Exception:
                pass

        Button(form, text="📷 Changer la photo", 
               command=lambda: self.choose_temp_photo(photo_preview),
               bg=self.colors['primary'], fg='white',
               font=('Segoe UI', 10), bd=0, padx=20, pady=8,
               cursor='hand2').pack(pady=10)
        
        fields = [
            ('Marque *', 'marque', v['marque']),
            ('Modèle *', 'modele', v['modele']),
            ('Immatriculation *', 'immat', v['immatriculation']),
            ('Prix/Jour (DH) *', 'prix', str(v['prix_jour'])),
        ]
        
        self.entries = {}
        for label, key, default_val in fields:
            Label(form, text=label, font=('Segoe UI', 11), anchor='w').pack(fill=X, pady=(15, 5))
            entry = Entry(form, font=('Segoe UI', 12), relief='solid', bd=1)
            entry.insert(0, default_val)
            entry.pack(fill=X, ipady=6)
            self.entries[key] = entry
        
        Label(form, text="Catégorie", font=('Segoe UI', 11), anchor='w').pack(fill=X, pady=(15, 5))
        self.cat_combo = ttk.Combobox(form, values=['Economique', 'Compacte', 'Premium'],
                                       font=('Segoe UI', 11), state='readonly')
        self.cat_combo.set(v['categorie'] if v['categorie'] in ['Economique', 'Compacte', 'Premium'] else 'Economique')
        self.cat_combo.pack(fill=X, ipady=4)

        # Statut + kilométrage
        statut_km_frame = Frame(form)
        statut_km_frame.pack(fill=X, pady=(10, 0))

        col_s = Frame(statut_km_frame)
        col_s.pack(side=LEFT, fill=X, expand=True)
        Label(col_s, text="Statut", font=('Segoe UI', 11), anchor='w').pack(anchor='w', pady=(0, 5))
        self.statut_combo = ttk.Combobox(col_s, values=['Disponible', 'Loué', 'Maintenance', 'Vendu'],
                                         font=('Segoe UI', 11), state='readonly')
        cur_statut = (v['statut'] if 'statut' in v.keys() else None) or 'Disponible'
        self.statut_combo.set(cur_statut if cur_statut in ['Disponible', 'Loué', 'Maintenance', 'Vendu'] else 'Disponible')
        self.statut_combo.pack(fill=X, ipady=4)

        col_k = Frame(statut_km_frame)
        col_k.pack(side=LEFT, fill=X, expand=True, padx=(15, 0))
        Label(col_k, text="Kilométrage", font=('Segoe UI', 11), anchor='w').pack(anchor='w', pady=(0, 5))
        self.km_entry = Entry(col_k, font=('Segoe UI', 12), relief='solid', bd=1)
        cur_km = v['kilometrage'] if 'kilometrage' in v.keys() else None
        if cur_km:
            self.km_entry.insert(0, str(int(cur_km)))
        self.km_entry.pack(fill=X, ipady=6)
        
        dates_frame = Frame(form)
        dates_frame.pack(fill=X, pady=15)
        
        self.date_entries = {}
        for i, (label, key, db_key) in enumerate([
            ('Assurance', 'assurance', 'date_assurance'),
            ('Visite Tech.', 'visite', 'date_visite'),
            ('Agrément', 'agrement', 'date_agrement')
        ]):
            col = Frame(dates_frame)
            col.pack(side=LEFT, fill=X, expand=True, padx=(0 if i==0 else 10, 0))
            
            Label(col, text=label, font=('Segoe UI', 10)).pack(anchor='w')
            entry = Entry(col, font=('Segoe UI', 10), relief='solid', bd=1)
            entry.insert(0, v[db_key] if v[db_key] else datetime.now().strftime('%Y-%m-%d'))
            entry.pack(fill=X, ipady=4)
            self.date_entries[key] = entry
        
        btn_frame = Frame(dialog, pady=20)
        btn_frame.pack(fill=X)
        
        Button(btn_frame, text="❌ Annuler", command=dialog.destroy,
               bg='#f4f6f9', fg='#7f8c8d',
               font=('Segoe UI', 11, 'bold'), bd=0, padx=30, pady=12,
               cursor='hand2').pack(side=LEFT, padx=30)
        
        Button(btn_frame, text="✅ Enregistrer les modifications", 
               command=lambda: self.save_edited_vehicule(dialog, v['id']),
               bg=self.colors['secondary'], fg='white',
               font=('Segoe UI', 11, 'bold'), bd=0, padx=30, pady=12, 
               cursor='hand2', activebackground='#0a7a5e').pack(side=RIGHT, padx=30)

    def save_edited_vehicule(self, dialog, veh_id):
        try:
            prix_val = self.entries['prix'].get().strip().replace(',', '.')
            prix_jour = float(prix_val) if prix_val else 0.0
        except ValueError:
            messagebox.showerror("Erreur de saisie", "Le prix journalier doit être un nombre valide.")
            return

        try:
            kilometrage = self._read_km_from_form()
        except ValueError:
            messagebox.showerror("Erreur de saisie", "Le kilométrage doit être un nombre valide.")
            return

        try:
            data = {
                'marque': self.entries['marque'].get().strip(),
                'modele': self.entries['modele'].get().strip(),
                'immat': self.entries['immat'].get().strip(),
                'categorie': self.cat_combo.get(),
                'prix': prix_jour,
                'assurance': self.date_entries['assurance'].get() or None,
                'visite': self.date_entries['visite'].get() or None,
                'agrement': self.date_entries['agrement'].get() or None,
                'photo_path': None,
                'statut': self.statut_combo.get(),
                'kilometrage': kilometrage
            }
            
            if not all([data['marque'], data['modele'], data['immat']]):
                messagebox.showwarning("Attention", "Veuillez remplir les champs obligatoires (*)")
                return
            
            self.db.update_vehicule(veh_id, data)
            
            if self.temp_photo_path:
                photo_path = save_vehicle_photo(self.temp_photo_path, veh_id)
                if photo_path:
                    self.db.update_vehicle_photo(veh_id, photo_path)
                    if veh_id in self.photo_cache:
                        del self.photo_cache[veh_id]
            
            messagebox.showinfo("Succès", "Véhicule modifié avec succès !")
            dialog.destroy()
            self.refresh_vehicles_list()
            
        except sqlite3.IntegrityError:
            messagebox.showerror("Doublon", "Un autre véhicule possède déjà cette plaque d'immatriculation.")
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible de modifier : {str(e)}")

    def delete_vehicule(self, veh_id):
        if messagebox.askyesno("Confirmation", "Supprimer ce véhicule ?\nLes locations associées seront également supprimées."):
            self.db.delete_vehicule(veh_id)
            if veh_id in self.photo_cache:
                del self.photo_cache[veh_id]
            self.refresh_vehicles_list()
            messagebox.showinfo("Supprimé", "Véhicule supprimé")

    # ============================================
    # LOCATIONS
    # ============================================
    def show_locations(self):
        self.clear_main()
        
        header = Frame(self.main_frame, bg=self.colors['bg'])
        header.pack(fill=X, pady=(0, 20))
        
        Label(header, text="🤝 Locations", 
              bg=self.colors['bg'], fg=self.colors['primary'],
              font=('Segoe UI', 28, 'bold')).pack(side=LEFT)
        
        Button(header, text="📄 Export CSV", command=self.export_locations_csv,
               bg='#3498db', fg='white', font=('Segoe UI', 11),
               bd=0, padx=20, pady=10, cursor='hand2').pack(side=RIGHT, padx=(0, 10))

        Button(header, text="+ Nouvelle location", command=self.add_location_dialog,
               bg=self.colors['secondary'], fg='white', font=('Segoe UI', 11),
               bd=0, padx=25, pady=10, cursor='hand2').pack(side=RIGHT)
        
        tree_frame = Frame(self.main_frame, bg='white', bd=1, relief='solid',
                          highlightbackground='#e1e8ed', highlightthickness=1)
        tree_frame.pack(fill=BOTH, expand=True)
        
        Frame(tree_frame, bg=self.colors['primary'], height=4).pack(fill=X)

        # Barre d'actions sur sélection
        actions_bar = Frame(tree_frame, bg='white')
        actions_bar.pack(fill=X, padx=10, pady=(8, 0))

        Button(actions_bar, text="✅ Clôturer (retour véhicule)",
               command=self.cloturer_location_selected,
               bg=self.colors['secondary'], fg='white', font=('Segoe UI', 10),
               bd=0, padx=15, pady=6, cursor='hand2').pack(side=LEFT, padx=(0, 8))

        Button(actions_bar, text="🗑️ Supprimer la location",
               command=self.delete_location_selected,
               bg='#fdeaea', fg=self.colors['danger'], font=('Segoe UI', 10),
               bd=0, padx=15, pady=6, cursor='hand2').pack(side=LEFT)

        Label(actions_bar, text="Astuce : double-cliquez sur une ligne pour la clôturer.",
              bg='white', fg='#7f8c8d', font=('Segoe UI', 9)).pack(side=RIGHT)
        
        columns = ('ID', 'Statut', 'Véhicule', 'Client', 'Début', 'Fin', 'Retour réel', 'Jours', 'Total', 'Paiement')
        tree = ttk.Treeview(tree_frame, columns=columns, show='headings', height=18)
        self.locations_tree = tree
        
        style = ttk.Style()
        style.configure("Treeview", font=('Segoe UI', 10), rowheight=35)
        style.configure("Treeview.Heading", font=('Segoe UI', 10, 'bold'), background=self.colors['bg'])
        style.configure('Loc.Turnaround.Horizontal.TProgressbar', troughcolor='#f4f6f9', background='#0d8b6d')

        tree.tag_configure('encours', background='#e8f4fd')
        tree.tag_configure('retard', background='#fdeaea')
        tree.tag_configure('avenir', background='#fef9e7')
        tree.tag_configure('terminee', foreground='#95a5a6')
        
        for col in columns:
            tree.heading(col, text=col)
            width = 55 if col == 'ID' else 100 if col in ('Jours', 'Paiement', 'Statut') else 140
            tree.column(col, width=width, anchor='center' if col in ('ID', 'Jours', 'Total', 'Statut') else 'w')
        
        scrollbar = ttk.Scrollbar(tree_frame, orient=VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=RIGHT, fill=Y)
        tree.pack(fill=BOTH, expand=True, padx=10, pady=10)

        tree.bind('<Double-1>', lambda e: self.cloturer_location_selected())
        
        locations = self.db.get_locations()
        tag_map = {'En cours': 'encours', 'Retard': 'retard',
                   'À venir': 'avenir', 'Terminée': 'terminee'}
        for l in locations:
            retour_reel = l['date_retour_reelle'] if 'date_retour_reelle' in l.keys() else None
            tree.insert('', END, iid=str(l['id']), tags=(tag_map.get(l['statut_loc'], ''),), values=(
                l['id'], l['statut_loc'],
                f"{l['marque']} {l['modele']}",
                l['client'], l['date_debut'], l['date_fin'],
                retour_reel or '-',
                l['jours'], f"{l['total']:,.0f} DH", l['paiement']
            ))

        # --- Taux de rotation du parc ---
        nb_veh = max(len(self.db.get_vehicules()), 1)
        jours_parc = sum((l['jours'] or 0) for l in locations)
        rotation = jours_parc / nb_veh
        rot_frame = Frame(self.main_frame, bg='white', bd=1, relief='solid',
                          highlightbackground='#e1e8ed', highlightthickness=1)
        rot_frame.pack(fill=X, pady=(12, 0))
        inner_rot = Frame(rot_frame, bg='white')
        inner_rot.pack(fill=X, padx=20, pady=12)
        Label(inner_rot, text=f"🔄 Rotation du parc : {rotation:,.0f} jour(s)-véhicule loué par véhicule"
              .replace(',', ' '),
              bg='white', fg=self.colors['primary'],
              font=('Segoe UI', 11, 'bold')).pack(anchor='w')
        pct = min(100, int(rotation / max(nb_veh * 30, 1) * 100 * 3))  # jauge indicative
        progress = ttk.Progressbar(inner_rot, value=max(pct, 2), maximum=100, length=400)
        progress.pack(anchor='w', pady=(6, 0), fill=X)
        
        total = self.db.get_total_locations()
        Label(self.main_frame, text=f"💰 TOTAL = {total:,.0f} DH", 
              bg=self.colors['bg'], fg=self.colors['primary'],
              font=('Segoe UI', 16, 'bold')).pack(anchor='e', pady=15)

    def _selected_location_id(self):
        sel = self.locations_tree.selection()
        if not sel:
            messagebox.showwarning("Sélection", "Veuillez sélectionner une location dans le tableau.")
            return None
        return int(sel[0])

    def cloturer_location_selected(self):
        loc_id = self._selected_location_id()
        if loc_id is None:
            return
        loc = self.db.get_location(loc_id)
        if not loc:
            return
        if loc['date_retour_reelle']:
            messagebox.showinfo("Déjà clôturée", "Cette location est déjà terminée.")
            return
        if messagebox.askyesno("Confirmer le retour",
                               f"Enregistrer le retour du véhicule pour la location #{loc_id}\n"
                               f"({loc['client']} — {loc['date_debut']} → {loc['date_fin']}) ?"):
            self.db.cloturer_location(loc_id)
            messagebox.showinfo("Succès", "Retour enregistré, véhicule remis disponible.")
            self.show_locations()

    def delete_location_selected(self):
        loc_id = self._selected_location_id()
        if loc_id is None:
            return
        if messagebox.askyesno("Confirmation", f"Supprimer définitivement la location #{loc_id} ?"):
            self.db.delete_location(loc_id)
            self.show_locations()

    def export_locations_csv(self):
        """Export des locations au format CSV (compatible Excel)."""
        path = filedialog.asksaveasfilename(
            title="Exporter les locations",
            defaultextension=".csv",
            initialfile=f"locations_{datetime.now().strftime('%Y-%m-%d')}.csv",
            filetypes=[("Fichier CSV", "*.csv")]
        )
        if not path:
            return
        try:
            import csv
            with open(path, 'w', newline='', encoding='utf-8-sig') as f:
                w = csv.writer(f, delimiter=';')
                w.writerow(['ID', 'Date location', 'Statut', 'Marque', 'Modele', 'Immatriculation',
                            'Client', 'Debut', 'Fin', 'Retour reel', 'Jours', 'Prix/jour', 'Total', 'Paiement'])
                for l in self.db.get_locations():
                    retour = l['date_retour_reelle'] if 'date_retour_reelle' in l.keys() else ''
                    w.writerow([l['id'], l['date_location'], l['statut_loc'], l['marque'], l['modele'],
                                l['immatriculation'], l['client'], l['date_debut'], l['date_fin'],
                                retour or '', l['jours'], l['prix_jour'], l['total'], l['paiement']])
            messagebox.showinfo("Export réussi", f"Fichier CSV créé :\n{path}")
        except Exception as e:
            messagebox.showerror("Erreur d'export", str(e))

    def add_location_dialog(self):
        dialog = Toplevel(self.root)
        dialog.title("Nouvelle Location")
        dialog.geometry("500x650")
        dialog.transient(self.root)
        
        Label(dialog, text="🤝 Nouvelle Location", 
              font=('Segoe UI', 18, 'bold'), fg=self.colors['primary']).pack(pady=20)
        
        # --- Utilisation de ScrollableFrame ---
        scroll_frame = ScrollableFrame(dialog, bg='#ffffff')
        scroll_frame.pack(fill=BOTH, expand=True, padx=30, pady=(0, 20))
        form = scroll_frame.scrollable_frame
        
        vehicules = self.db.get_vehicules()
        if not vehicules:
            messagebox.showwarning("Attention", "Aucun véhicule disponible.")
            dialog.destroy()
            return
        
        veh_dict = {}
        veh_options = []
        for v in vehicules:
            label = f"{v['marque']} {v['modele']} ({v['immatriculation']}) - {v['prix_jour']} DH/j"
            veh_dict[label] = v
            veh_options.append(label)
        
        Label(form, text="Véhicule : ", font=('Segoe UI', 11)).pack(anchor='w', pady=(15, 5))
        veh_combo = ttk.Combobox(form, values=veh_options, font=('Segoe UI', 11), state='readonly')
        veh_combo.pack(fill=X, ipady=4)
        
        photo_preview = Label(form, text="Sélectionnez un véhicule", 
                             bg='#f4f6f9', fg='#bdc3c7',
                             font=('Segoe UI', 12), height=8)
        photo_preview.pack(fill=X, pady=10)
        
        def update_preview(event):
            selected = veh_combo.get()
            if selected in veh_dict:
                v = veh_dict[selected]
                if v['photo_path'] and os.path.exists(v['photo_path']):
                    try:
                        img = Image.open(v['photo_path'])
                        resample_method = getattr(Image, 'Resampling', Image).LANCZOS
                        img.thumbnail((400, 150), resample_method)
                        photo = ImageTk.PhotoImage(img)
                        photo_preview.configure(image=photo, text="", bg='white')
                        photo_preview.image = photo
                    except Exception:
                        pass
                else:
                    photo_preview.configure(image="", text="🚗 Pas de photo", bg='#f4f6f9', fg='#bdc3c7')
                    photo_preview.image = None
        
        veh_combo.bind('<<ComboboxSelected>>', update_preview)

        def on_veh_select(event):
            selected = veh_combo.get()
            if selected in veh_dict:
                prix_entry.delete(0, END)
                prix_entry.insert(0, str(veh_dict[selected]['prix_jour']))
            calculer()
        veh_combo.bind('<<ComboboxSelected>>', on_veh_select)
        
        fields = [('Client *', 'client'), ('Date location', 'date')]
        entries = {}
        
        for label, key in fields:
            Label(form, text=label, font=('Segoe UI', 11)).pack(anchor='w', pady=(15, 5))
            entry = Entry(form, font=('Segoe UI', 12), relief='solid', bd=1)
            if key == 'date':
                entry.insert(0, datetime.now().strftime('%Y-%m-%d'))
            entry.pack(fill=X, ipady=6)
            entries[key] = entry
        
        dates_frame = Frame(form)
        dates_frame.pack(fill=X, pady=15)
        
        date_entries = {}
        today_d = datetime.now()
        tomorrow_d = today_d + timedelta(days=1)
        for i, (label, key) in enumerate([('Date Début *', 'debut'), ('Date Fin *', 'fin')]):
            col = Frame(dates_frame)
            col.pack(side=LEFT, fill=X, expand=True, padx=(0 if i==0 else 10, 0))
            
            Label(col, text=label, font=('Segoe UI', 10)).pack(anchor='w')
            entry = Entry(col, font=('Segoe UI', 10), relief='solid', bd=1)
            entry.insert(0, (today_d if i == 0 else tomorrow_d).strftime('%Y-%m-%d'))
            entry.pack(fill=X, ipady=4)
            date_entries[key] = entry
        
        Label(form, text="Prix/Jour (DH) : ", font=('Segoe UI', 11)).pack(anchor='w', pady=(15, 5))
        prix_entry = Entry(form, font=('Segoe UI', 12), relief='solid', bd=1)
        prix_entry.pack(fill=X, ipady=6)

        Label(form, text="Acompte (DH) : ", font=('Segoe UI', 11)).pack(anchor='w', pady=(10, 5))
        acompte_entry = Entry(form, font=('Segoe UI', 12), relief='solid', bd=1)
        acompte_entry.insert(0, '0')
        acompte_entry.pack(fill=X, ipady=6)

        Label(form, text="Notes : ", font=('Segoe UI', 11)).pack(anchor='w', pady=(10, 5))
        notes_txt = Text(form, font=('Segoe UI', 10), height=3, relief='solid', bd=1)
        notes_txt.pack(fill=X)
        
        total_lbl = Label(form, text="Total : 0 DH", 
                         font=('Segoe UI', 14, 'bold'), fg=self.colors['primary'])
        total_lbl.pack(pady=15)
        
        def calculer():
            try:
                d1 = datetime.strptime(date_entries['debut'].get(), '%Y-%m-%d')
                d2 = datetime.strptime(date_entries['fin'].get(), '%Y-%m-%d')
                jours = max(1, (d2 - d1).days) if d2 > d1 else (d2 - d1).days
                
                prix_val = prix_entry.get().replace(',', '.')
                prix = float(prix_val) if prix_val else 0.0
                
                j = max(0, jours) if d2 >= d1 else max(0, (d2 - d1).days)
                total = j * prix
                total_lbl.configure(text=f"Total : {total:,.0f} DH ({j} jours)")
                return j, total
            except Exception:
                total_lbl.configure(text="Total : Dates invalides")
                return None, None

        # Calcul automatique en temps réel
        for e in list(date_entries.values()) + [prix_entry]:
            e.bind('<KeyRelease>', lambda ev: calculer())
        
        Button(form, text="📅 Recalculer", command=calculer,
               bg='#3498db', fg='white', font=('Segoe UI', 10),
               bd=0, padx=20, pady=8, cursor='hand2').pack(pady=10)
        
        Label(form, text="Paiement : ", font=('Segoe UI', 11)).pack(anchor='w')
        paiement = ttk.Combobox(form, values=['Espèces', 'Carte', 'Virement'],
                                font=('Segoe UI', 11), state='readonly')
        paiement.set('Espèces')
        paiement.pack(fill=X, ipady=4)
        
        conflict_lbl = Label(form, text="", bg='white', fg=self.colors['danger'],
                             font=('Segoe UI', 10, 'bold'))
        conflict_lbl.pack(pady=(8, 0))

        def check_conflict(*_):
            selected = veh_combo.get()
            if selected not in veh_dict:
                conflict_lbl.configure(text="")
                return False
            try:
                debut = date_entries['debut'].get()
                fin = date_entries['fin'].get()
                datetime.strptime(debut, '%Y-%m-%d')
                datetime.strptime(fin, '%Y-%m-%d')
            except Exception:
                conflict_lbl.configure(text="")
                return False
            if self.db.has_date_conflict(veh_dict[selected]['id'], debut, fin):
                conflict_lbl.configure(
                    text=f"⛔ Conflit : ce véhicule est déjà loué sur cette période !")
                return True
            conflict_lbl.configure(text="✅ Véhicule disponible sur cette période",
                                   fg=self.colors['secondary'])
            return False

        for e in list(date_entries.values()):
            e.bind('<KeyRelease>', check_conflict)
        veh_combo.bind('<<ComboboxSelected>>', check_conflict)
        
        def save():
            jours, total = calculer()
            if jours is None:
                messagebox.showerror("Erreur de date", "Veuillez vérifier le format (AAAA-MM-JJ).")
                return
            if jours <= 0:
                messagebox.showerror("Erreur de date",
                                     "La date de fin doit être postérieure à la date de début.")
                return
            
            try:
                selected = veh_combo.get()
                if not selected:
                    messagebox.showwarning("Attention", "Veuillez sélectionner un véhicule.")
                    return

                client = entries['client'].get().strip()
                if not client:
                    messagebox.showwarning("Attention", "Le nom du client est obligatoire.")
                    return

                if check_conflict():
                    if not messagebox.askyesno(
                            "Conflit de dates",
                            "Ce véhicule est déjà loué sur cette période.\nForcer l'enregistrement ?"):
                        return

                prix_val = prix_entry.get().replace(',', '.')
                acompte_val = acompte_entry.get().strip().replace(',', '.') or '0'
                
                data = {
                    'date': entries['date'].get(),
                    'vehicule_id': veh_dict[selected]['id'],
                    'client': client,
                    'debut': date_entries['debut'].get(),
                    'fin': date_entries['fin'].get(),
                    'jours': jours,
                    'prix': float(prix_val) if prix_val else 0.0,
                    'total': total,
                    'paiement': paiement.get(),
                    'acompte': float(acompte_val),
                    'notes': notes_txt.get('1.0', END).strip() or None
                }
                self.db.add_location(data)
                messagebox.showinfo("Succès", "Location enregistrée !")
                dialog.destroy()
                self.show_locations()
            except ValueError:
                messagebox.showerror("Erreur", "Le prix et l'acompte doivent être numériques.")
            except Exception as e:
                messagebox.showerror("Erreur", str(e))
        
        Button(form, text="✅ Enregistrer", command=save,
               bg=self.colors['secondary'], fg='white',
               font=('Segoe UI', 12, 'bold'), bd=0, padx=30, pady=12,
               cursor='hand2').pack(pady=20)

    # ============================================
    # CHARGES
    # ============================================
    def show_charges(self):
        self.clear_main()

        header = Frame(self.main_frame, bg=self.colors['bg'])
        header.pack(fill=X)
        Label(header, text="💰 Gestion des Charges",
              bg=self.colors['bg'], fg=self.colors['primary'],
              font=('Segoe UI', 28, 'bold')).pack(side=LEFT, pady=(0, 20))

        Button(header, text="+ Nouvelle charge variable",
               command=self.add_variable_charge_dialog,
               bg='#3498db', fg='white', font=('Segoe UI', 10),
               bd=0, padx=15, pady=8, cursor='hand2').pack(side=RIGHT, pady=(0, 20))

        total_fixes, total_vars, total_pers, total_all = self.db.get_total_charges()

        synth = Frame(self.main_frame, bg='white', bd=1, relief='solid',
                      highlightbackground='#e1e8ed', highlightthickness=1)
        synth.pack(fill=X, pady=(0, 12))
        inner = Frame(synth, bg='white')
        inner.pack(fill=X, padx=20, pady=10)
        for txt, val in [("Charges fixes :", f"{total_fixes:,.0f} DH".replace(',', ' ')),
                         ("Charges variables :", f"{total_vars:,.0f} DH".replace(',', ' ')),
                         ("Personnel :", f"{total_pers:,.0f} DH".replace(',', ' ')),
                         ("TOTAL charges :", f"{total_all:,.0f} DH".replace(',', ' '))]:
            Label(inner, text=txt, bg='white', fg='#7f8c8d',
                  font=('Segoe UI', 11)).pack(side=LEFT, padx=(0, 6))
            Label(inner, text=val, bg='white', fg=self.colors['danger'],
                  font=('Segoe UI', 11, 'bold')).pack(side=LEFT, padx=(0, 25))
        
        notebook = ttk.Notebook(self.main_frame)
        notebook.pack(fill=BOTH, expand=True, pady=10)
        
        tab_fixes = Frame(notebook, bg=self.colors['bg'])
        notebook.add(tab_fixes, text="   Charges Fixes    ")
        
        Button(tab_fixes, text="+ Ajouter charge fixe", 
               command=self.add_fixed_charge_dialog,
               bg=self.colors['secondary'], fg='white', font=('Segoe UI', 10),
               bd=0, padx=15, pady=8, cursor='hand2').pack(anchor='e', pady=10)
        
        self.create_charges_table(tab_fixes, self.db.get_charges_fixes(), 'fixe')
        
        tab_vars = Frame(notebook, bg=self.colors['bg'])
        notebook.add(tab_vars, text="   Charges Variables    ")
        
        self.create_charges_table(tab_vars, self.db.get_charges_variables(), 'variable')

    def create_charges_table(self, parent, data, charge_type):
        if charge_type == 'fixe':
            columns = ('Date', 'Véhicule', 'Type', 'Montant', 'Période')
        else:
            columns = ('Date', 'Véhicule', 'Type', 'Montant', 'Description')
            
        tree = ttk.Treeview(parent, columns=columns, show='headings', height=15)
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=150)
        tree.pack(fill=BOTH, expand=True, padx=10, pady=10)
        
        for row in data:
            vehicule_name = f"{row['marque'] or ''} {row['modele'] or ''}".strip() or "N/A"
            if charge_type == 'fixe':
                values = (row['date_charge'], vehicule_name, row['type'], f"{row['montant']:,.0f} DH", row['periode'] or '-')
            else:
                values = (row['date_charge'], vehicule_name, row['type'], f"{row['montant']:,.0f} DH", row['description'] or '-')
            tree.insert('', END, iid=str(row['id']), values=values)

        # Suppression par touche Suppr
        def on_delete(event):
            sel = tree.selection()
            if not sel:
                return
            if messagebox.askyesno("Confirmation", "Supprimer cette charge ?"):
                if charge_type == 'fixe':
                    self.db.conn.execute("DELETE FROM charges_fixes WHERE id=?", (sel[0],))
                else:
                    self.db.conn.execute("DELETE FROM charges_variables WHERE id=?", (sel[0],))
                self.db.conn.commit()
                self.show_charges()
        tree.bind('<Delete>', on_delete)

    def add_variable_charge_dialog(self):
        dialog = Toplevel(self.root)
        dialog.title("Ajouter une Charge Variable")
        dialog.geometry("420x420")
        dialog.transient(self.root)
        dialog.grab_set()

        scroll_frame = ScrollableFrame(dialog, bg='#ffffff')
        scroll_frame.pack(fill=BOTH, expand=True, padx=30, pady=20)
        form = scroll_frame.scrollable_frame

        Label(form, text="Date :", font=('Segoe UI', 11)).pack(anchor='w', pady=(5, 0))
        date_entry = Entry(form, font=('Segoe UI', 11))
        date_entry.insert(0, datetime.now().strftime('%Y-%m-%d'))
        date_entry.pack(fill=X, pady=(0, 12))

        Label(form, text="Véhicule concerné :", font=('Segoe UI', 11)).pack(anchor='w', pady=(0, 0))
        vehicules = self.db.get_vehicules()
        veh_labels = ['Général (sans véhicule)'] + [
            f"{v['marque']} {v['modele']} ({v['immatriculation']})" for v in vehicules]
        veh_combo = ttk.Combobox(form, values=veh_labels, state='readonly', font=('Segoe UI', 11))
        veh_combo.current(0)
        veh_combo.pack(fill=X, pady=(0, 12))

        Label(form, text="Type (ex: Carburant, Réparation) :", font=('Segoe UI', 11)).pack(anchor='w')
        type_entry = Entry(form, font=('Segoe UI', 11))
        type_entry.pack(fill=X, pady=(0, 12))

        Label(form, text="Montant (DH) :", font=('Segoe UI', 11)).pack(anchor='w')
        montant_entry = Entry(form, font=('Segoe UI', 11))
        montant_entry.pack(fill=X, pady=(0, 12))

        Label(form, text="Description :", font=('Segoe UI', 11)).pack(anchor='w')
        desc_entry = Entry(form, font=('Segoe UI', 11))
        desc_entry.pack(fill=X, pady=(0, 20))

        def save():
            try:
                montant_val = montant_entry.get().replace(',', '.')
                montant = float(montant_val)
                if montant <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Erreur", "Le montant doit être un nombre positif.")
                return
            if not type_entry.get().strip():
                messagebox.showwarning("Attention", "Veuillez saisir le type de charge.")
                return
            veh_id = None
            idx = veh_combo.current()
            if idx > 0 and vehicules:
                veh_id = vehicules[idx - 1]['id']
            data = {
                'date': date_entry.get(),
                'vehicule_id': veh_id,
                'type': type_entry.get().strip(),
                'montant': montant,
                'description': desc_entry.get().strip() or None
            }
            try:
                self.db.add_charge_variable(data)
                messagebox.showinfo("Succès", "Charge variable ajoutée !")
                dialog.destroy()
                self.show_charges()
            except Exception as e:
                messagebox.showerror("Erreur", str(e))

        Button(form, text="✅ Enregistrer", command=save,
               bg=self.colors['secondary'], fg='white', font=('Segoe UI', 11, 'bold'),
               bd=0, padx=20, pady=10, cursor='hand2').pack()

    def add_fixed_charge_dialog(self):
        dialog = Toplevel(self.root)
        dialog.title("Ajouter une Charge Fixe")
        dialog.geometry("400x400")
        dialog.transient(self.root)
        dialog.grab_set()
        
        # --- Utilisation de ScrollableFrame ---
        scroll_frame = ScrollableFrame(dialog, bg='#ffffff')
        scroll_frame.pack(fill=BOTH, expand=True, padx=30, pady=20)
        form = scroll_frame.scrollable_frame
        
        Label(form, text="Date :", font=('Segoe UI', 11)).pack(anchor='w', pady=(5, 0))
        date_entry = Entry(form, font=('Segoe UI', 11))
        date_entry.insert(0, datetime.now().strftime('%Y-%m-%d'))
        date_entry.pack(fill=X, pady=(0, 15))
        
        Label(form, text="Type de charge (ex: Assurance, Loyer) :", font=('Segoe UI', 11)).pack(anchor='w', pady=(5, 0))
        type_entry = Entry(form, font=('Segoe UI', 11))
        type_entry.pack(fill=X, pady=(0, 15))
        
        Label(form, text="Montant (DH) :", font=('Segoe UI', 11)).pack(anchor='w', pady=(5, 0))
        montant_entry = Entry(form, font=('Segoe UI', 11))
        montant_entry.pack(fill=X, pady=(0, 15))
        
        Label(form, text="Période (ex: Mensuel, Annuel) :", font=('Segoe UI', 11)).pack(anchor='w', pady=(5, 0))
        periode_entry = Entry(form, font=('Segoe UI', 11))
        periode_entry.pack(fill=X, pady=(0, 25))
        
        def save():
            try:
                montant_val = montant_entry.get().replace(',', '.')
                data = {
                    'date': date_entry.get(),
                    'type': type_entry.get(),
                    'montant': float(montant_val),
                    'periode': periode_entry.get()
                }
                if not data['type'] or not data['montant']:
                    messagebox.showwarning("Attention", "Veuillez remplir le type et le montant.")
                    return
                
                self.db.add_charge_fixe(data)
                messagebox.showinfo("Succès", "Charge fixe ajoutée !")
                dialog.destroy()
                self.show_charges()
            except ValueError:
                 messagebox.showerror("Erreur", "Le montant doit être un nombre.")
            except Exception as e:
                messagebox.showerror("Erreur", f"Données invalides : {str(e)}")
                
        Button(form, text="✅ Enregistrer", command=save,
               bg=self.colors['secondary'], fg='white', font=('Segoe UI', 11, 'bold'),
               bd=0, padx=20, pady=10, cursor='hand2').pack()

    # ============================================
    # PERSONNEL
    # ============================================
    def show_personnel(self):
        self.clear_main()

        header = Frame(self.main_frame, bg=self.colors['bg'])
        header.pack(fill=X)
        Label(header, text="👥 Charges Personnel",
              bg=self.colors['bg'], fg=self.colors['primary'],
              font=('Segoe UI', 28, 'bold')).pack(side=LEFT, pady=(0, 20))

        Button(header, text="+ Ajouter une paie",
               command=self.add_personnel_dialog,
               bg=self.colors['secondary'], fg='white', font=('Segoe UI', 10),
               bd=0, padx=15, pady=8, cursor='hand2').pack(side=RIGHT, pady=(0, 20))

        tree = ttk.Treeview(self.main_frame,
                            columns=('Date', 'Nom', 'Poste', 'Montant', 'Description'),
                            show='headings', height=20)
        for col in ('Date', 'Nom', 'Poste', 'Montant', 'Description'):
            tree.heading(col, text=col)
            tree.column(col, width=200)
        tree.pack(fill=BOTH, expand=True, padx=20, pady=(10, 5))

        total_paies = 0
        for p in self.db.get_personnel():
            keys = p.keys()
            nom = p['nom'] if 'nom' in keys else None
            poste = p['poste'] if 'poste' in keys else None
            total_paies += p['montant'] or 0
            tree.insert('', END, iid=str(p['id']),
                        values=(p['date_paie'], nom or '-', poste or '-',
                                f"{p['montant']:,.0f} DH", p['description'] or '-'))

        Label(self.main_frame, text=f"💸 TOTAL paies : {total_paies:,.0f} DH".replace(',', ' '),
              bg=self.colors['bg'], fg=self.colors['danger'],
              font=('Segoe UI', 14, 'bold')).pack(anchor='e', padx=20, pady=(0, 15))

        def on_delete(event):
            sel = tree.selection()
            if not sel:
                return
            if messagebox.askyesno("Confirmation", "Supprimer cette ligne de paie ?"):
                self.db.delete_personnel(sel[0])
                self.show_personnel()
        tree.bind('<Delete>', on_delete)

    def add_personnel_dialog(self):
        dialog = Toplevel(self.root)
        dialog.title("Ajouter une paie")
        dialog.geometry("400x430")
        dialog.transient(self.root)
        dialog.grab_set()

        form = Frame(dialog, bg='white')
        form.pack(fill=BOTH, expand=True, padx=30, pady=20)

        Label(form, text="Date de paie :", font=('Segoe UI', 11)).pack(anchor='w', pady=(5, 0))
        date_entry = Entry(form, font=('Segoe UI', 11))
        date_entry.insert(0, datetime.now().strftime('%Y-%m-%d'))
        date_entry.pack(fill=X, pady=(0, 12))

        Label(form, text="Nom (optionnel) :", font=('Segoe UI', 11)).pack(anchor='w')
        nom_entry = Entry(form, font=('Segoe UI', 11))
        nom_entry.pack(fill=X, pady=(0, 12))

        Label(form, text="Poste (optionnel) :", font=('Segoe UI', 11)).pack(anchor='w')
        poste_entry = Entry(form, font=('Segoe UI', 11))
        poste_entry.pack(fill=X, pady=(0, 12))

        Label(form, text="Montant (DH) * :", font=('Segoe UI', 11)).pack(anchor='w')
        montant_entry = Entry(form, font=('Segoe UI', 11))
        montant_entry.pack(fill=X, pady=(0, 12))

        Label(form, text="Description :", font=('Segoe UI', 11)).pack(anchor='w')
        desc_entry = Entry(form, font=('Segoe UI', 11))
        desc_entry.pack(fill=X, pady=(0, 20))

        def save():
            try:
                montant = float(montant_entry.get().replace(',', '.'))
                if montant <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Erreur", "Le montant doit être un nombre positif.")
                return
            data = {
                'date': date_entry.get(),
                'montant': montant,
                'description': desc_entry.get().strip() or None,
                'nom': nom_entry.get().strip() or None,
                'poste': poste_entry.get().strip() or None,
            }
            try:
                self.db.add_personnel(data)
                messagebox.showinfo("Succès", "Paie enregistrée !")
                dialog.destroy()
                self.show_personnel()
            except Exception as e:
                messagebox.showerror("Erreur", str(e))

        Button(form, text="✅ Enregistrer", command=save,
               bg=self.colors['secondary'], fg='white', font=('Segoe UI', 11, 'bold'),
               bd=0, padx=20, pady=10, cursor='hand2').pack()

    # ============================================
    # ALERTES
    # ============================================
    def show_alertes(self):
        self.clear_main()

        header = Frame(self.main_frame, bg=self.colors['bg'])
        header.pack(fill=X)
        Label(header, text="⚠️ Alertes Documents",
              bg=self.colors['bg'], fg=self.colors['primary'],
              font=('Segoe UI', 28, 'bold')).pack(side=LEFT, pady=(0, 20))

        Button(header, text="📄 Export CSV", command=self.export_alertes_csv,
               bg='#3498db', fg='white', font=('Segoe UI', 10),
               bd=0, padx=15, pady=8, cursor='hand2').pack(side=RIGHT, pady=(0, 20))

        alertes = self.db.get_alertes()
        
        if not alertes:
            frame = Frame(self.main_frame, bg='#e8f8f5', bd=1, relief='solid')
            frame.pack(fill=X, pady=50, ipady=30)
            Label(frame, text="✅ Tous les documents sont à jour !", 
                  bg='#e8f8f5', fg='#0d8b6d',
                  font=('Segoe UI', 16, 'bold')).pack()
            return
        
        for a in alertes:
            is_expired = a['statut'] == 'EXPIRÉ'
            bg_color = '#fdeaea' if is_expired else '#fef5e7'
            border_color = '#dc3545' if is_expired else '#f39c12'
            
            card = Frame(self.main_frame, bg=bg_color, bd=0,
                        highlightbackground=border_color, highlightthickness=2)
            card.pack(fill=X, pady=8, ipady=15)
            
            left = Frame(card, bg=bg_color)
            left.pack(side=LEFT, padx=20)
            
            if a['photo_path'] and os.path.exists(a['photo_path']):
                thumb = get_photo_thumbnail(a['photo_path'], (80, 60))
                if thumb:
                    lbl = Label(left, image=thumb, bg=bg_color)
                    lbl.image = thumb
                    lbl.pack(side=LEFT, padx=(0, 15))
            
            info = Frame(left, bg=bg_color)
            info.pack(side=LEFT)
            
            Label(info, text=f"🚗 {a['voiture']}", bg=bg_color,
                  font=('Segoe UI', 14, 'bold'), fg=self.colors['primary']).pack(anchor='w')
            Label(info, text=f"🆔 {a['immat']}  |  📄 {a['document']}", 
                  bg=bg_color, font=('Segoe UI', 11)).pack(anchor='w', pady=(5, 0))
            
            right = Frame(card, bg=bg_color)
            right.pack(side=RIGHT, padx=20)
            
            Label(right, text=a['expiration'], bg=bg_color,
                  font=('Segoe UI', 12), fg=border_color).pack()
            
            jours_text = f"EXPIRÉ depuis {abs(a['jours'])} jours" if is_expired else f"{a['jours']} jours restants"
            Label(right, text=jours_text, bg=bg_color,
                  font=('Segoe UI', 10), fg=border_color).pack(pady=(5, 0))
            
            Label(right, text=a['statut'], bg=border_color, fg='white',
                  font=('Segoe UI', 10, 'bold'), padx=15, pady=5).pack(pady=(10, 0))

    # ============================================
    # BACKUPS
    # ============================================
    def show_backups(self):
        self.clear_main()
        
        Label(self.main_frame, text="💾 Gestion des Sauvegardes", 
              bg=self.colors['bg'], fg=self.colors['primary'],
              font=('Segoe UI', 28, 'bold')).pack(anchor='w', pady=(0, 20))
        
        info = Frame(self.main_frame, bg='#e8f4fd', bd=1, relief='solid',
                  highlightbackground='#3498db', highlightthickness=1)
        info.pack(fill=X, pady=10, ipady=15)
        
        Label(info, text="ℹ️ Un backup est créé automatiquement à chaque fermeture de l'application.\nLes backups de plus de 30 jours sont supprimés automatiquement.", 
              bg='#e8f4fd', fg='#1e3a5f',
              font=('Segoe UI', 11), justify=LEFT).pack(padx=20)
        
        Button(info, text="🔄 Créer un backup maintenant", command=self.manual_backup,
               bg='#3498db', fg='white', font=('Segoe UI', 11),
               bd=0, padx=20, pady=10, cursor='hand2').pack(pady=10)
        
        Label(self.main_frame, text="Backups disponibles :", 
              bg=self.colors['bg'], fg=self.colors['primary'],
              font=('Segoe UI', 16, 'bold')).pack(anchor='w', pady=(20, 10))
        
        backups = list_backups()
        
        if not backups:
            Label(self.main_frame, text="Aucun backup trouvé", 
                  bg=self.colors['bg'], fg='#7f8c8d').pack(pady=20)
            return
        
        for b in backups:
            row = Frame(self.main_frame, bg='white', bd=1, relief='solid') 
            row.pack(fill=X, pady=5, ipady=12)
            
            Label(row, text=f"📁 {b['filename']}", bg='white',
                  font=('Segoe UI', 11, 'bold')).pack(side=LEFT, padx=20)
            Label(row, text=f"📅 {b['date']}", bg='white',
                  font=('Segoe UI', 10)).pack(side=LEFT, padx=20)
            Label(row, text=f"💾 {b['size']}", bg='white',
                  font=('Segoe UI', 10), fg='#7f8c8d').pack(side=LEFT, padx=20)
            
            Button(row, text="↩️ Restaurer", 
                   command=lambda p=b['path']: self.restore_backup(p),
                   bg='#f39c12', fg='white', font=('Segoe UI', 9),
                   bd=0, padx=15, pady=6, cursor='hand2').pack(side=RIGHT, padx=10)
            
            Button(row, text="📂 Ouvrir dossier", 
                   command=self.open_backup_folder,
                   bg='#f4f6f9', fg=self.colors['primary'],
                   font=('Segoe UI', 9), bd=0, padx=15, pady=6,
                   cursor='hand2').pack(side=RIGHT, padx=10)

    def open_backup_folder(self):
        try:
            if sys.platform == "win32":
                os.startfile(BACKUP_DIR)
            elif sys.platform == "darwin":
                subprocess.call(["open", BACKUP_DIR])
            else:
                subprocess.call(["xdg-open", BACKUP_DIR])
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible d'ouvrir le dossier : {str(e)}")

    def manual_backup(self):
        path = create_backup()
        if path:
            messagebox.showinfo("Backup créé", f"Sauvegarde créée :\n{path}")
            self.show_backups()

    def restore_backup(self, backup_path):
        if messagebox.askyesno("⚠️ Confirmer", 
                               "Cela remplacera TOUTES les données actuelles.\nUne sauvegarde de l'état actuel sera faite avant.\n\nContinuer ?"):
            if restore_backup(backup_path):
                messagebox.showinfo("Succès", "Backup restauré !\nL'application va se fermer. Rouvrez-la.")
                self.root.destroy()
            else:
                messagebox.showerror("Erreur", "Impossible de restaurer le backup.")

# ============================================
# LANCEMENT
# ============================================
if __name__ == "__main__":
    init_db()
    root = Tk()

    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass

    app = Application(root)
    root.mainloop()