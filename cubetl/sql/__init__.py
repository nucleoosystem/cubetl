import logging
from lxml import etree
from sqlalchemy.engine import create_engine
from sqlalchemy.schema import Table, MetaData, Column
from sqlalchemy.types import Integer, String, Float, Boolean, Unicode
import sys
from cubetl.core import Node, Component
from sqlalchemy.sql.expression import and_
from cubetl.functions.text import parsebool

# Get an instance of a logger
logger = logging.getLogger(__name__)

class Connection(Component):
    
    def __init__(self):
        
        super(Connection, self).__init__()
        
        self.url = None
        self._engine = None
        self._connection = None
        

    def lazy_init(self):
        if (self._engine == None):
                self._engine = create_engine(self.url)
                self._connection = self._engine.connect()
                
    def connection(self):
        self.lazy_init()
        return self._connection
    
    def engine(self):
        self.lazy_init()
        return self._engine

class SQLTable(Component):
    
    _selects = 0
    _inserts = 0
    _finalized = False
    
    def __init__(self):

        super(SQLTable, self).__init__()

        self._pk = False

        self.name = None
        self.connection = None
        self.columns = [ ]
        
        self.create = True
        
        self.sa_table = None
        self.sa_metadata = None
        
        self._selects = 0
        self._inserts = 0
        self._unicode_errors = 0
    
    def _get_sa_type(self, column):
        
        if (column["type"] == "Integer"):
            return Integer
        elif (column["type"] == "String"):
            if (not "length" in column): column["length"] = 128
            return Unicode(length = column["length"])
        elif (column["type"] == "Float"):
            return Float    
        elif (column["type"] == "Boolean"):
            return Boolean
        elif (column["type"] == "AutoIncrement"):
            return Integer
        else:
            raise Exception("Invalid data type: %s" % column["type"])
    
    def finalize(self, ctx):
        
        if (not SQLTable._finalized):
            SQLTable._finalized = True
            logger.info("SQLTable Totals  inserts/selects: %d/%d " % 
                        (SQLTable._inserts, SQLTable._selects))
        
        logger.info("SQLTable %-18s inserts/selects: %6d/%-6d " % 
                        (self.name, self._inserts, self._selects))
        if (self._unicode_errors > 0):
            logger.warn("SQLTable %s found %d warnings assigning non-unicode fields to unicode columns" % 
                        (self.name, self._unicode_errors))
        
        ctx.comp.finalize(self.connection)     
        
        super(SQLTable, self).finalize(ctx)
    
    def initialize(self, ctx):
        
        super(SQLTable, self).initialize(ctx)
        
        ctx.comp.initialize(self.connection) 
        
        logger.debug("Loading table %s on %r" % (self.name, self))
        
        self.sa_metadata = MetaData()
        self.sa_table = Table(self.name, self.sa_metadata)

        # Drop?

        # Columns
        for column in self.columns:
            column["pk"] = False if (not "pk" in column) else parsebool(column["pk"])
            if (not "type" in column): column["type"] = "String"
            #if (not "value" in column): column["value"] = None
            logger.debug("Adding column %s" % column)
            self.sa_table.append_column( Column(column["name"], 
                                                self._get_sa_type(column), 
                                                primary_key = column["pk"], 
                                                autoincrement = (True if column["type"] == "AutoIncrement" else False) ))
        
        # Check schema
        
        # Create if doesn't exist
        if (not self.connection.engine().has_table(self.name)):
            logger.info("Creating table %s" % self.name) 
            self.sa_table.create(self.connection.connection())
            
        # Extend?
        
        # Delete columns?
                
            
    def pk(self, ctx):
        """
        Returns the primary key column definitToClauion, or None if none defined.
        """
        
        if (self._pk == False):
            pk_cols = []
            for col in self.columns:
                if ("pk" in col):
                    if parsebool(col["pk"]):
                        pk_cols.append(col)
                        
            if (len(pk_cols) > 1):
                raise Exception("Table %s has multiple primary keys: %s" % (self.name, pk_cols))
            elif (len(pk_cols) == 1):
                self._pk = pk_cols[0]
            else:
                self._pk = None
                
        return self._pk
            
    def _attribsToClause(self, attribs):
        clauses = []
        for k, v in attribs.items():
            if isinstance(v, (list, tuple)):
                clauses.append(self.sa_table.c[k].in_(v))
            else:
                clauses.append(self.sa_table.c[k] == v)
        
        return and_(*clauses)            
            
    def _rowtodict(self, row):
        
        d = {}
        for column in self.columns:
            d[column["name"]] = getattr(row, column["name"])
    
        return d
            
    def find(self, ctx, attribs):
        
        self._selects = self._selects + 1
        SQLTable._selects = SQLTable._selects + 1
        
        query = self.sa_table.select(self._attribsToClause(attribs))
        rows = self.connection.connection().execute(query)

        for r in rows:
            # Ensure we return dicts, not RowProxys from SqlAlchemy
            yield self._rowtodict(r)
             
        
    def findone(self, ctx, attribs):
        
        if (len(attribs.keys()) == 0):
            raise Exception("Searching on table with no criteria (empty attribute set)")
        
        rows = self.find(ctx, attribs)
        rows = list(rows)
        if (len(rows) > 1):
            raise Exception("Found 0 or more than one row when searching for just one in table %s: %s" % (self.name, attribs))
        elif (len(rows) == 1):
            row = rows[0]   
        else:
            row = None
        
        logger.debug("Findone result on %s: %s = %s" % (self.name, attribs, row))
        return row
    
    def store(self, ctx, data, keys = []):
        
        # Use primary key if available
        pk = self.pk(ctx)
        if ((pk != None) and (pk["name"] in data)):
            keys = [pk["name"]]
        
        # Use keys
        qfilter = {}
        for key in keys:
            try:
                qfilter[key] = data[key]
            except KeyError, e:
                raise Exception("Could not find attibute '%s' in data when storing row data: %s" % (key, data))
        
        row = None
        if (len(qfilter.keys()) > 0):
            row = self.findone(ctx, qfilter)
        
        if (row):
            # TODO
            return row
        
        rid =  self.insert(ctx, data)
                        
        return rid
        
    def _prepare_row(self, ctx, data):
        
        row = {}
        
        for column in self.columns:
            if (column["type"] != "AutoIncrement"):
                try:
                    row[column["name"]] = data[column["name"]]
                except KeyError, e:
                    raise Exception("Missing attribute for column %s in table '%s' while inserting row: %s" % (e, self.name, data))
                
                # Checks
                if ((column["type"] == "String") and (not isinstance(row[column["name"]], unicode))):
                    self._unicode_errors = self._unicode_errors + 1 
                    if (ctx.debug):
                        logger.warn("Unicode column %r received non-unicode string: %r " % (column["name"], row[column["name"]]))
                
        return row
    
    def insert(self, ctx, data):
        
        row = self._prepare_row(ctx, data)
        
        logger.debug ("Inserting table '%s' row: %s" % (self.name, row))
        res = self.connection.connection().execute(self.sa_table.insert(row))

        pk = self.pk(ctx)
        row[pk["name"]] = res.inserted_primary_key[0]
        
        self._inserts = self._inserts +1
        SQLTable._inserts = SQLTable._inserts + 1 
        
        if (pk != None):
            return row
        else:
            return None

class Transaction(Node):
    
    def __init__(self):
        
        super(Transaction, self).__init__()
        
        self.connection = None
        
        self._transaction = None
        
        self.enabled = True

    def initialize(self, ctx):
        
        super(Transaction, self).initialize(ctx)
        self.enabled = parsebool(self.enabled)

    def finalize(self, ctx):
        if (self.enabled):
            logger.info("Commiting database transaction")
            self._transaction.commit()
            self._transaction = None
        
    def process(self, ctx, m):
        
        # Store
        if (self._transaction != None):
            raise Exception("Trying to start transaction while one already exists is not supported")
        
        if (self.enabled):
            logger.info("Starting database transaction")
            self._transaction = self.connection.connection().begin()
        else:
            logger.debug("Not starting database transaction (Transaction node is disabled)")
        
        yield m        
        
        
class StoreRow(Node):
    
    def __init__(self):
        
        super(StoreRow, self).__init__()
        
        self.table = None
    
    def process(self, ctx, m):
        
        # Store
        self.table.store(ctx, m)
        
        yield m        
