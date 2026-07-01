import sqlite3
import json
import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple

try:
    from database import get_connection
except ImportError:
    # Fallback for standalone testing or if database.py is not strictly required for logic
    def get_connection():
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        return conn

class EntityManager:
    """
    Manages multiple businesses/entities under one Tether account.
    Handles entity creation, user permissions, and aggregate statistics.
    """

    def ensure_tables(self) -> None:
        """Create entities and entity_users tables if they don't exist."""
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            abn TEXT,
            address TEXT,
            phone TEXT,
            email TEXT,
            logo_url TEXT,
            payment_terms_days INTEGER DEFAULT 30,
            late_fee_percent REAL DEFAULT 2.0,
            late_fee_days_grace INTEGER DEFAULT 7,
            stripe_account_id TEXT,
            settings_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS entity_users (
            id TEXT PRIMARY KEY,
            entity_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            role TEXT CHECK(role IN ('admin', 'manager', 'viewer')) NOT NULL,
            permissions_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE
        )
        """)

        # Note: entity_debtors is expected to link to main debtors table
        # For this module, we assume it exists or is created elsewhere, 
        # but we will create it here for completeness if possible.
        try:
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS entity_debtors (
                id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                debtor_id TEXT NOT NULL,
                FOREIGN KEY (entity_id) REFERENCES entities(id) ON DELETE CASCADE,
                UNIQUE(entity_id, debtor_id)
            )
            """)
        except sqlite3.OperationalError:
            pass  # Table might not be fully compatible if debtors table is strictly defined elsewhere

        conn.commit()

    def create_entity(self, name: str, abn: str = "", settings: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Create a new entity/business with default settings.
        """
        entity_id = f"ent-{uuid.uuid4()}"
        now = datetime.now().isoformat()
        
        default_settings = {
            "payment_terms_days": 30,
            "late_fee_percent": 2.0,
            "late_fee_days_grace": 7
        }
        
        if settings:
            default_settings.update(settings)

        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
            INSERT INTO entities (id, name, abn, settings_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """, (
                entity_id,
                name,
                abn,
                json.dumps(default_settings),
                now
            ))
            conn.commit()
            
            return {
                "id": entity_id,
                "name": name,
                "abn": abn,
                "settings": default_settings,
                "created_at": now,
                "message": f"Entity '{name}' created successfully."
            }
        except sqlite3.IntegrityError as e:
            return {"error": "Entity creation failed", "details": str(e)}
        except Exception as e:
            return {"error": "Unexpected error", "details": str(e)}
        finally:
            conn.close()

    def get_entity(self, entity_id: str) -> Dict[str, Any]:
        """
        Return entity details with computed stats.
        """
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM entities WHERE id = ?", (entity_id,))
        entity_row = cursor.fetchone()
        
        if not entity_row:
            return {"error": "Entity not found"}
            
        entity = dict(entity_row)
        entity['settings'] = json.loads(entity.get('settings_json') or '{}')
        
        # Compute stats
        try:
            # Assuming debtors table exists and is linked via entity_debtors
            cursor.execute("""
            SELECT COUNT(d.id) as total_debtors, 
                   COALESCE(SUM(d.amount_cents), 0) as total_outstanding,
                   SUM(CASE WHEN d.status = 'overdue' THEN 1 ELSE 0 END) as overdue_count
            FROM debtors d
            JOIN entity_debtors ed ON d.id = ed.debtor_id
            WHERE ed.entity_id = ?
            """, (entity_id,))
            
            stats = cursor.fetchone()
            if stats:
                entity['stats'] = {
                    "total_debtors": stats['total_debtors'],
                    "total_outstanding": stats['total_outstanding'],
                    "overdue_count": stats['overdue_count']
                }
            else:
                entity['stats'] = {"total_debtors": 0, "total_outstanding": 0, "overdue_count": 0}
                
        except sqlite3.OperationalError:
            # Fallback if debtors table or view doesn't exist exactly as expected
            entity['stats'] = {
                "total_debtors": 0,
                "total_outstanding": 0,
                "overdue_count": 0,
                "note": "Stats unavailable due to schema mismatch"
            }
        finally:
            conn.close()
            
        return entity

    def list_entities(self, user_id: str = "") -> List[Dict[str, Any]]:
        """
        List all entities, optionally filtered by user access.
        """
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        entities = []
        
        if user_id:
            cursor.execute("""
            SELECT e.* FROM entities e
            JOIN entity_users eu ON e.id = eu.entity_id
            WHERE eu.user_id = ?
            """, (user_id,))
        else:
            cursor.execute("SELECT * FROM entities")
            
        rows = cursor.fetchall()
        
        for row in rows:
            entity = dict(row)
            entity['settings'] = json.loads(entity.get('settings_json') or '{}')
            entities.append(entity)
            
        conn.close()
        return entities

    def add_user(self, entity_id: str, user_id: str, role: str = "viewer") -> Dict[str, Any]:
        """
        Add a user to an entity with role-based permissions.
        """
        if role not in ['admin', 'manager', 'viewer']:
            return {"error": "Invalid role. Must be admin, manager, or viewer."}
            
        user_entity_id = f"ue-{uuid.uuid4()}"
        now = datetime.now().isoformat()
        
        # Define default permissions based on role
        permissions = {
            "admin": {"create": True, "read": True, "update": True, "delete": True, "manage_users": True},
            "manager": {"create": True, "read": True, "update": True, "delete": False, "manage_users": False},
            "viewer": {"create": False, "read": True, "update": False, "delete": False, "manage_users": False}
        }
        
        conn = get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
            INSERT INTO entity_users (id, entity_id, user_id, role, permissions_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                user_entity_id,
                entity_id,
                user_id,
                role,
                json.dumps(permissions[role]),
                now
            ))
            conn.commit()
            return {
                "id": user_entity_id,
                "entity_id": entity_id,
                "user_id": user_id,
                "role": role,
                "permissions": permissions[role],
                "message": f"User {user_id} added as {role}."
            }
        except sqlite3.IntegrityError:
            return {"error": "User already assigned to this entity or invalid entity/user ID."}
        except Exception as e:
            return {"error": str(e)}
        finally:
            conn.close()

    def get_entity_summary(self, entity_id: str) -> Dict[str, Any]:
        """
        Return summary statistics for an entity.
        """
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        summary = {
            "entity_id": entity_id,
            "total_debtors": 0,
            "total_outstanding_cents": 0,
            "paid_this_month_cents": 0,
            "overdue_count": 0,
            "avg_days_overdue": 0,
            "dso": 0
        }
        
        try:
            # Get current month start
            now = datetime.now()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            
            # 1. Total Debtors and Outstanding
            cursor.execute("""
            SELECT COUNT(d.id) as count, COALESCE(SUM(d.amount_cents), 0) as outstanding
            FROM debtors d
            JOIN entity_debtors ed ON d.id = ed.debtor_id
            WHERE ed.entity_id = ?
            """, (entity_id,))
            
            row = cursor.fetchone()
            if row:
                summary['total_debtors'] = row['count']
                summary['total_outstanding_cents'] = row['outstanding']
                
            # 2. Overdue Count
            cursor.execute("""
            SELECT COUNT(d.id) as count
            FROM debtors d
            JOIN entity_debtors ed ON d.id = ed.debtor_id
            WHERE ed.entity_id = ? AND d.status = 'overdue'
            """, (entity_id,))
            
            row = cursor.fetchone()
            if row:
                summary['overdue_count'] = row['count']
                
            # 3. Paid this month (assuming events or transactions table tracks payments)
            # Simplified logic: check events table for 'payment' type in current month
            cursor.execute("""
            SELECT COALESCE(SUM(e.amount_cents), 0) as paid
            FROM events e
            JOIN entity_debtors ed ON e.debtor_id = ed.debtor_id
            WHERE ed.entity_id = ? 
              AND e.type = 'payment'
              AND e.created_at >= ?
            """, (entity_id, month_start.isoformat()))
            
            row = cursor.fetchone()
            if row:
                summary['paid_this_month_cents'] = row['paid']

            # 4. Avg Days Overdue (Simplified calculation)
            # We would need due_date and current_date logic here
            # For now, returning 0 as placeholder or querying if data exists
            summary['avg_days_overdue'] = 0
            
            # 5. DSO (Days Sales Outstanding)
            # Formula: (Accounts Receivable / Total Credit Sales) * Number of Days
            # Simplified: Total Outstanding / (Paid this month + Total Outstanding) * 30
            total_sales_proxy = summary['total_outstanding_cents'] + summary['paid_this_month_cents']
            if total_sales_proxy > 0:
                summary['dso'] = int((summary['total_outstanding_cents'] / total_sales_proxy) * 30)
            else:
                summary['dso'] = 0
                
        except sqlite3.OperationalError:
            return {"error": "Database schema mismatch. Ensure debtors and events tables exist."}
        finally:
            conn.close()
            
        return summary

    def switch_entity(self, entity_id: str) -> Dict[str, Any]:
        """
        Return a context dict for switching the current view to another entity.
        """
        entity = self.get_entity(entity_id)
        if 'error' in entity:
            return entity
            
        # Simulate recent activity (last 5 events)
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        recent_activity = []
        try:
            # Assuming events table exists
            cursor.execute("""
            SELECT * FROM events e
            JOIN entity_debtors ed ON e.debtor_id = ed.debtor_id
            WHERE ed.entity_id = ?
            ORDER BY e.created_at DESC
            LIMIT 5
            """, (entity_id,))
            
            for row in cursor.fetchall():
                recent_activity.append(dict(row))
        except sqlite3.OperationalError:
            pass
            
        conn.close()
        
        return {
            "entity_name": entity['name'],
            "debtor_count": entity['stats'].get('total_debtors', 0),
            "outstanding_total": entity['stats'].get('total_outstanding', 0),
            "recent_activity": recent_activity
        }

    def list_entity_stats(self) -> List[Dict[str, Any]]:
        """
        Aggregate stats across all entities for a multi-entity dashboard.
        """
        conn = get_connection()
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        stats_list = []
        
        # Fetch all entities
        cursor.execute("SELECT * FROM entities")
        entities = cursor.fetchall()
        
        for entity in entities:
            entity_id = entity['id']
            stats = self.get_entity_summary(entity_id)
            
            # Placeholder for trend calculation (e.g., compared to last month)
            trend = 0.0 
            alert_count = stats.get('overdue_count', 0)
            
            stats_list.append({
                "entity_name": entity['name'],
                "debtor_count": stats.get('total_debtors', 0),
                "outstanding": stats.get('total_outstanding_cents', 0),
                "dso": stats.get('dso', 0),
                "trend": trend,
                "alert_count": alert_count
            })
            
        conn.close()
        return stats_list
