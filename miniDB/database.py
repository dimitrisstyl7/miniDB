from __future__ import annotations
import pickle
from pprint import pprint
from time import sleep, localtime, strftime
import os,sys
import logging
import warnings
import readline
from tabulate import tabulate

sys.path.append(f'{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}/miniDB')
from miniDB import table
sys.modules['table'] = table

from joins import Inlj, Smj
from btree import Btree
from extendible_hashing import ExtendibleHashing
from table import Table


# readline.clear_history()

class Database:
    '''
    Main Database class, containing tables.
    '''

    def __init__(self, name, load=True, verbose = True):
        self.tables = {}
        self._name = name
        self.verbose = verbose
        self.stats = {} # dictionary of statistics for each table.
        self.savedir = f'dbdata/{name}_db'

        if load:
            try:
                self.load_database()
                self.load_statistics()
                self.calculate_tables_statistics() # update statistics.
                logging.info(f'Loaded "{name}".')
                return
            except:
                if verbose:
                    warnings.warn(f'Database "{name}" does not exist. Creating new.')

        # create dbdata directory if it doesn't exist
        if not os.path.exists('dbdata'):
            os.mkdir('dbdata')

        # create new dbs save directory
        try:
            os.mkdir(self.savedir)
            os.mkdir(f'{self.savedir}/stats_dir') # create stats directory.
        except:
            pass

        # create all the meta tables
        self.create_table('meta_length', 'table_name,no_of_rows', 'str,int')
        self.create_table('meta_locks', 'table_name,pid,mode', 'str,int,str')
        self.create_table('meta_insert_stack', 'table_name,indexes', 'str,list')
        self.create_table('meta_indexes', 'table_name,indexed_column,index_name,index_type', 'str,str,str,str')
        self.save_database()

    def save_database(self):
        '''
        Save database as a pkl file. This method saves the database object, including all tables and attributes.
        '''
        for name, table in self.tables.items():
            with open(f'{self.savedir}/{name}.pkl', 'wb') as f:
                pickle.dump(table, f)

    def _save_locks(self):
        '''
        Stores the meta_locks table to file as meta_locks.pkl.
        '''
        with open(f'{self.savedir}/meta_locks.pkl', 'wb') as f:
            pickle.dump(self.tables['meta_locks'], f)

    def load_database(self):
        '''
        Load all tables that are part of the database (indices noted here are loaded).

        Args:
            <> path: string. Directory (path) of the database on the system.
        '''
        path = f'dbdata/{self._name}_db'
        for file in os.listdir(path):

            if file[-3:]!='pkl': # if used to load only pkl files
                continue
            f = open(path+'/'+file, 'rb')
            tmp_dict = pickle.load(f)
            f.close()
            name = f'{file.split(".")[0]}'
            self.tables.update({name: tmp_dict})
            # setattr(self, name, self.tables[name])

    #### IO ####

    def _update(self):
        '''
        Update all meta tables.
        '''
        self._update_meta_length()
        self._update_meta_insert_stack()

    def create_table(self, name, column_names, column_types, primary_key=None, unique_columns=None, load=None):
        '''
        This method create a new table. This table is saved and can be accessed via db_object.tables['table_name'] or db_object.table_name

        Args:
            <> name: string. Name of table.
            <> column_names: list. Names of columns.
            <> column_types: list. Types of columns.
            <> primary_key: string. The primary key (if it exists).
            <> unique_columns: list. List of columns that are unique.
            <> load: boolean. Defines table object parameters as the name of the table and the column names.
        '''
        # print('here -> ', column_names.split(','))
            
        self.tables.update(
            { name: Table(name=name, column_names=column_names.split(','),
                         column_types=column_types.split(','),
                         primary_key=primary_key,
                         unique_columns=unique_columns.split(',') if unique_columns is not None else None,
                         load=load) }
            )
        
        # self._name = Table(name=name, column_names=column_names, column_types=column_types, load=load)
        # check that new dynamic var doesnt exist already
        # self.no_of_tables += 1
        self._update()
        self.calculate_tables_statistics() # update statistics.
        self.save_database()
        # (self.tables[name])
        if self.verbose:
            print(f'Created table "{name}".')

    def drop_table(self, table_name):
        '''
        Drop table from current database.

        Args:
            <> table_name: string. Name of table.
        '''
        self.load_database()
        self.lock_table(table_name)

        self.tables.pop(table_name)
        if os.path.isfile(f'{self.savedir}/{table_name}.pkl'):
            os.remove(f'{self.savedir}/{table_name}.pkl')
            self.calculate_tables_statistics() # update statistics.
        else:
            warnings.warn(f'"{self.savedir}/{table_name}.pkl" not found.')
        self.delete_from('meta_locks', f'table_name={table_name}')
        self.delete_from('meta_length', f'table_name={table_name}')
        self.delete_from('meta_insert_stack', f'table_name={table_name}')

        if self._has_index(table_name):
            to_be_deleted = []
            for key, table in enumerate(self.tables['meta_indexes'].column_by_name('table_name')):
                if table == table_name:
                    to_be_deleted.append(key)

            for i in reversed(to_be_deleted):
                self.drop_index(self.tables['meta_indexes'].data[i][2]) # index name

        try:
            delattr(self, table_name)
        except AttributeError:
            pass
        # self._update()
        self.save_database()

    def import_table(self, table_name, filename, column_types=None, primary_key=None):
        '''
        Creates table from CSV file.

        Args:
            <> filename: string. CSV filename. If not specified, filename's name will be used.
            <> column_types: list. Types of columns. If not specified, all will be set to type str.
            <> primary_key: string. The primary key (if it exists).
        '''
        file = open(filename, 'r')

        first_line=True
        for line in file.readlines():
            if first_line:
                colnames = line.strip('\n')
                if column_types is None:
                    column_types = ",".join(['str' for _ in colnames.split(',')])
                self.create_table(name=table_name, column_names=colnames, column_types=column_types, primary_key=primary_key)
                lock_ownership = self.lock_table(table_name, mode='x')
                first_line = False
                continue
            self.tables[table_name]._insert(line.strip('\n').split(','))

        if lock_ownership:
             self.unlock_table(table_name)
        self._update()
        self.save_database()

    def export(self, table_name, filename=None):
        '''
        Transform table to CSV.

        Args:
            <> table_name: string. Name of table.
            <> filename: string. Output CSV filename.
        '''
        res = ''
        for row in [self.tables[table_name].column_names]+self.tables[table_name].data:
            res+=str(row)[1:-1].replace('\'', '').replace('"','').replace(' ','')+'\n'

        if filename is None:
            filename = f'{table_name}.csv'

        with open(filename, 'w') as file:
           file.write(res)

    def table_from_object(self, new_table):
        '''
        Add table object to database.

        Args:
            <> new_table: string. Name of new table.
        '''

        self.tables.update({new_table._name: new_table})
        if new_table._name not in self.__dir__():
            setattr(self, new_table._name, new_table)
        else:
            raise Exception(f'"{new_table._name}" attribute already exists in class "{self.__class__.__name__}".')
        self._update()
        self.save_database()


    ##### table functions #####

    # In every table function a load command is executed to fetch the most recent table.
    # In every table function, we first check whether the table is locked. Since we have implemented
    # only the X lock, if the tables is locked we always abort.
    # After every table function, we update and save. Update updates all the meta tables and save saves all
    # tables.

    # these function calls are named close to the ones in postgres

    def cast(self, column_name, table_name, cast_type):
        '''
        Modify the type of the specified column and cast all prexisting values.
        (Executes type() for every value in column and saves)

        Args:
            <> table_name: string. Name of table (must be part of database).
            <> column_name: string. The column that will be casted (must be part of database).
            <> cast_type: type. Cast type (do not encapsulate in quotes).
        '''
        self.load_database()
        
        lock_ownership = self.lock_table(table_name, mode='x')
        self.tables[table_name]._cast_column(column_name, eval(cast_type))
        if lock_ownership:
            self.unlock_table(table_name)
        self._update()
        self.save_database()

    def insert_into(self, table_name, row_str):
        '''
        Inserts data to given table.

        Args:
            <> table_name: string. Name of table (must be part of database).
            <> row: list. A list of values to be inserted (will be casted to a predifined type automatically).
            <> lock_load_save: boolean. If False, user needs to load, lock and save the states of the database (CAUTION). Useful for bulk-loading.
        '''
        row = row_str.strip().split(',')
        self.load_database()
        # fetch the insert_stack. For more info on the insert_stack
        # check the insert_stack meta table
        lock_ownership = self.lock_table(table_name, mode='x')
        insert_stack = self._get_insert_stack_for_table(table_name)
        try:
            self.tables[table_name]._insert(row, insert_stack)
        except Exception as e:
            logging.info(e)
            logging.info('ABORTED')
            if lock_ownership: # if we locked the table, we need to unlock it before raising the exception
                self.unlock_table(table_name)
            raise e # abort and raise exception
        self._update_meta_insert_stack_for_tb(table_name, insert_stack[:-1])

        if lock_ownership:
            self.unlock_table(table_name)
        self._update()
        self.save_database()

    def update(self, table_name, set_args, condition):
        '''
        Update the value of a column where a condition is met.

        Args:
            <> table_name: string. Name of table (must be part of database).
            <> set_value: string. New value of the predifined column name.
            <> set_column: string. The column to be altered.
            <> condition: string or dict (the condition is the returned dic['where'] from interpret method).
                Operatores supported: (<,<=,=,>=,>)
        '''
        set_column, set_value = set_args.replace(' ','').split('=')
        self.load_database()
        
        lock_ownership = self.lock_table(table_name, mode='x')
        self.tables[table_name]._update_rows(set_value, set_column, condition)
        if lock_ownership:
            self.unlock_table(table_name)
        self._update()
        self.save_database()

    def delete_from(self, table_name, condition):
        '''
        Delete rows of table where condition is met.

        Args:
            <> table_name: string. Name of table (must be part of database).
            <> condition: string or dict (the condition is the returned dic['where'] from interpret method).
                Operatores supported: (<,<=,=,>=,>)
        '''
        self.load_database()
        
        lock_ownership = self.lock_table(table_name, mode='x')
        deleted = self.tables[table_name]._delete_where(condition)
        if lock_ownership:
            self.unlock_table(table_name)
        self._update()
        self.save_database()
        # we need the save above to avoid loading the old database that still contains the deleted elements
        if table_name[:4]!='meta':
            self._add_to_insert_stack(table_name, deleted)
        self.save_database()

    def select(self, columns, table_name, condition, distinct=None, order_by=None, limit=True, desc=None, save_as=None, return_object=True):
        '''
        Selects and outputs a table's data where condtion is met.

        Args:
            <> table_name: string. Name of table (must be part of database).
            <> columns: list. The columns that will be part of the output table (use '*' to select all available columns)
            <> condition: string or dict (the condition is the returned dic['where'] from interpret method).
                Operatores supported: (<,<=,=,>=,>)
            <> order_by: string. A column name that signals that the resulting table should be ordered based on it (no order if None).
            <> desc: boolean. If True, order_by will return results in descending order (True by default).
            <> limit: int. An integer that defines the number of rows that will be returned (all rows if None).
            <> save_as: string. The name that will be used to save the resulting table into the database (no save if None).
            <> return_object: boolean. If True, the result will be a table object (useful for internal use - the result will be printed by default).
            <> distinct: boolean. If True, the resulting table will contain only unique rows.
        '''
        self.load_database()
        if isinstance(table_name, Table): # if table_name is a table object
            return table_name._select_where(columns, condition, distinct, order_by, desc, limit)
        
        # self.lock_table(table_name, mode='x')
        if self.is_locked(table_name):
            return
        
        # if table has an index and a condition is given.
        if self._has_index(table_name) and condition is not None:
            # Table object of 'meta_indexes' table which contains the indexes of the specified table.
            table_indexes = self.select('*', 'meta_indexes', f'table_name={table_name}', return_object=True)
            
            # get the indexes name for the specified table.
            index_name_list = table_indexes.column_by_name('index_name')
            
            # get the indexed columns name for the specified table.
            indexed_column_list = table_indexes.column_by_name('indexed_column')
            
            # get the indexed column type (btree or hash) for the specified table. 
            indexed_type_list = table_indexes.column_by_name('index_type')
            
            btree_list = []
            hash_list = []
            
            # create a list of dictionaries with the indexed column name as key and the index object (btree or hash) as value.
            for i in range(len(index_name_list)):
                if indexed_type_list[i] == 'btree':
                    btree_list.append( { indexed_column_list[i]: self._load_idx(index_name_list[i]) } )
                else: # indexed_type_list[i] == 'hash'
                    hash_list.append( { indexed_column_list[i]: self._load_idx(index_name_list[i]) } )
            
            # create a dictionary with the indexed column name as key and the value of the condition as value.
            btree_dic=None
            hash_dic=None
            
            if btree_list:
                btree_dic = {k: v for d in btree_list for k, v in d.items()}
            
            if hash_list:
                hash_dic = {k: v for d in hash_list for k, v in d.items()}
            
            table = self.tables[table_name]._select_where(columns, condition, distinct, order_by, desc, limit, btree_dic, hash_dic)
        else:
            table = self.tables[table_name]._select_where(columns, condition, distinct, order_by, desc, limit)
        # self.unlock_table(table_name)
        if save_as is not None:
            table._name = save_as
            self.table_from_object(table)
        else:
            if return_object:
                return table
            else:
                return table.show()

    def show_table(self, table_name, no_of_rows=None):
        '''
        Print table in a readable tabular design (using tabulate).

        Args:
            <> table_name: string. Name of table (must be part of database).
        '''
        self.load_database()
        
        self.tables[table_name].show(no_of_rows, self.is_locked(table_name))

    def sort(self, table_name, column_name, asc=False):
        '''
        Sorts a table based on a column.

        Args:
            <> table_name: string. Name of table (must be part of database).
            <> column_name: string. the column name that will be used to sort.
            <> asc: If True sort will return results in ascending order (False by default).
        '''

        self.load_database()
        
        lock_ownership = self.lock_table(table_name, mode='x')
        self.tables[table_name]._sort(column_name, asc=asc)
        if lock_ownership:
            self.unlock_table(table_name)
        self._update()
        self.save_database()

    def create_view(self, table_name, table):
        '''
        Create a virtual table based on the result-set of the SQL statement provided.

        Args:
            <> table_name: string. Name of the table that will be saved.
            <> table: table. The table that will be saved.
        '''
        table._name = table_name
        self.table_from_object(table)

    def join(self, mode, left_table, right_table, condition, save_as=None, return_object=True):
        '''
        Join two tables that are part of the database where condition is met.

        Args:
            <> left_table: string. Name of the left table (must be in DB) or Table obj.
            <> right_table: string. Name of the right table (must be in DB) or Table obj.
            <> condition: string. A condition using the following format:
                'column[<,<=,==,>=,>]value' or
                'value[<,<=,==,>=,>]column'.
                
                Operators supported: (<,<=,==,>=,>)
        save_as: string. The output filename that will be used to save the resulting table in the database (won't save if None).
        return_object: boolean. If True, the result will be a table object (useful for internal usage - the result will be printed by default).
        '''
        self.load_database()
        if self.is_locked(left_table) or self.is_locked(right_table):
            return

        left_table = left_table if isinstance(left_table, Table) else self.tables[left_table] 
        right_table = right_table if isinstance(right_table, Table) else self.tables[right_table] 


        if mode=='inner':
            res = left_table._inner_join(right_table, condition)
        
        elif mode=='left':
            res = left_table._left_join(right_table, condition)
        
        elif mode=='right':
            res = left_table._right_join(right_table, condition)
        
        elif mode=='full':
            res = left_table._full_join(right_table, condition)

        elif mode=='inl':
            # Check if there is an index of either of the two tables available, as if there isn't we can't use inlj
            leftIndexExists = self._has_index(left_table._name)
            rightIndexExists = self._has_index(right_table._name)
            column_exist_r = False
            column_exist_l = False
            
            if not leftIndexExists and not rightIndexExists:
                res = None
                raise Exception('Index-nested-loop join cannot be executed. Use inner join instead.\n')

            if rightIndexExists:
                # Get the 'meta_indexes' table object, which contains the indexes of the right table.
                table_indexes_r = self.select('*', 'meta_indexes', f'table_name={right_table._name}', return_object=True)
                
                # Check if the indexed column of the right table is the same as the condition column ('on' clause).
                column_name = condition.split('=')[0]
                column_exist_r = [right_table._name, column_name] in [[row[0], row[1]] for row in table_indexes_r.data]

            if leftIndexExists:
                # Get the 'meta_indexes' table object, which contains the indexes of the left table.
                table_indexes_l = self.select('*', 'meta_indexes', f'table_name={left_table._name}', return_object=True)
                
                # Check if the indexed column of the left table is the same as the condition column ('on' clause).
                column_name = condition.split('=')[0]
                column_exist_l = [left_table._name, column_name] in [[row[0], row[1]] for row in table_indexes_l.data]

            if column_exist_r:
                # If the column exists in the right table, get the specific index name and use it to join the tables.
                for row in table_indexes_r.data:
                    if row[0]==right_table._name and row[1]==column_name:
                        index_name = row[2] # Get the index name
                        break
                res = Inlj(condition, left_table, right_table, self._load_idx(index_name), 'right').join()

            elif column_exist_l:
            # If the column exists in the left table, get the specific index name and use it to join the tables.
                for row in table_indexes_l.data:
                    if row[0]==left_table._name and row[1]==column_name:
                        index_name = row[2] # Get the index name
                        break
                res = Inlj(condition, left_table, right_table, self._load_idx(index_name), 'left').join()

        elif mode=='sm':
            res = Smj(condition, left_table, right_table).join()

        else:
            raise NotImplementedError

        if save_as is not None:
            res._name = save_as
            self.table_from_object(res)
        else:
            if return_object:
                return res
            else:
                res.show()

        if return_object:
            return res
        else:
            res.show()
        
    def lock_table(self, table_name, mode='x'):
        '''
        Locks the specified table using the exclusive lock (X).

        Args:
            <> table_name: string. Table name (must be part of database).
        '''
        if table_name[:4]=='meta' or table_name not in self.tables.keys() or isinstance(table_name,Table):
            return

        with open(f'{self.savedir}/meta_locks.pkl', 'rb') as f:
            self.tables.update({'meta_locks': pickle.load(f)})

        try:
            pid = self.tables['meta_locks']._select_where('pid',f'table_name={table_name}').data[0][0]
            if pid!=os.getpid():
                raise Exception(f'Table "{table_name}" is locked by process with pid={pid}')
            else:
                return False

        except IndexError:
            pass

        if mode=='x':
            self.tables['meta_locks']._insert([table_name, os.getpid(), mode])
        else:
            raise NotImplementedError
        self._save_locks()
        return True
        # print(f'Locking table "{table_name}"')

    def unlock_table(self, table_name, force=False):
        '''
        Unlocks the specified table that is exclusively locked (X).

        Args:
            <> table_name: string. Table name (must be part of database).
        '''
        if table_name not in self.tables.keys():
            raise Exception(f'Table "{table_name}" is not in database')

        if not force:
            try:
                # pid = self.select('*','meta_locks',  f'table_name={table_name}', return_object=True).data[0][1]
                pid = self.tables['meta_locks']._select_where('pid',f'table_name={table_name}').data[0][0]
                if pid!=os.getpid():
                    raise Exception(f'Table "{table_name}" is locked by the process with pid={pid}')
            except IndexError:
                pass
        self.tables['meta_locks']._delete_where(f'table_name={table_name}')
        self._save_locks()
        # print(f'Unlocking table "{table_name}"')

    def is_locked(self, table_name):
        '''
        Check whether the specified table is exclusively locked (X).

        Args:
            <> table_name: string. Table name (must be part of database).
        '''
        if isinstance(table_name,Table) or table_name[:4]=='meta':  # meta tables will never be locked (they are internal)
            return False

        with open(f'{self.savedir}/meta_locks.pkl', 'rb') as f:
            self.tables.update({'meta_locks': pickle.load(f)})

        try:
            pid = self.tables['meta_locks']._select_where('pid',f'table_name={table_name}').data[0][0]
            if pid!=os.getpid():
                raise Exception(f'Table "{table_name}" is locked by the process with pid={pid}')

        except IndexError:
            pass
        return False


    #### META ####

    # The following functions are used to update, alter, load and save the meta tables.
    # Important: Meta tables contain info regarding the NON meta tables ONLY.
    # i.e. meta_length will not show the number of rows in meta_locks etc.

    def _update_meta_length(self):
        '''
        Updates the meta_length table.
        '''
        for table in self.tables.values():
            if table._name[:4]=='meta': #skip meta tables
                continue
            if table._name not in self.tables['meta_length'].column_by_name('table_name'): # if new table, add record with 0 no. of rows
                self.tables['meta_length']._insert([table._name, 0])

            # the result needs to represent the rows that contain data. Since we use an insert_stack
            # some rows are filled with Nones. We skip these rows.
            non_none_rows = len([row for row in table.data if any(row)])
            self.tables['meta_length']._update_rows(non_none_rows, 'no_of_rows', f'table_name={table._name}')
            # self.update_row('meta_length', len(table.data), 'no_of_rows', 'table_name', '==', table._name)

    def _update_meta_locks(self):
        '''
        Updates the meta_locks table.
        '''
        for table in self.tables.values():
            if table._name[:4]=='meta': #skip meta tables
                continue
            if table._name not in self.tables['meta_locks'].column_by_name('table_name'):

                self.tables['meta_locks']._insert([table._name, False])
                # self.insert('meta_locks', [table._name, False])

    def _update_meta_insert_stack(self):
        '''
        Updates the meta_insert_stack table.
        '''
        for table in self.tables.values():
            if table._name[:4]=='meta': #skip meta tables
                continue
            if table._name not in self.tables['meta_insert_stack'].column_by_name('table_name'):
                self.tables['meta_insert_stack']._insert([table._name, []])

    def _add_to_insert_stack(self, table_name, indexes):
        '''
        Adds provided indices to the insert stack of the specified table.

        Args:
            <> table_name: string. Table name (must be part of database).
            <> indexes: list. The list of indices that will be added to the insert stack (the indices of the newly deleted elements).
        '''
        old_lst = self._get_insert_stack_for_table(table_name)
        self._update_meta_insert_stack_for_tb(table_name, old_lst+indexes)

    def _get_insert_stack_for_table(self, table_name):
        '''
        Returns the insert stack of the specified table.

        Args:
            <> table_name: string. Table name (must be part of database).
        '''
        return self.tables['meta_insert_stack']._select_where('*', f'table_name={table_name}').column_by_name('indexes')[0]
        # res = self.select('meta_insert_stack', '*', f'table_name={table_name}', return_object=True).indexes[0]
        # return res

    def _update_meta_insert_stack_for_tb(self, table_name, new_stack):
        '''
        Replaces the insert stack of a table with the one supplied by the user.

        Args:
            <> table_name: string. Table name (must be part of database).
            <> new_stack: string. The stack that will be used to replace the existing one.
        '''
        self.tables['meta_insert_stack']._update_rows(new_stack, 'indexes', f'table_name={table_name}')

    def save_statistics(self):
        '''
        Save statistics to file.
        '''
        with open(f'{self.savedir}/stats_dir/stats.pkl', 'wb') as f:
            pickle.dump(self.stats, f)

    def load_statistics(self):
        '''
        Load statistics from file.
        '''
        path = f'{self.savedir}/stats_dir/stats.pkl'
        try:
            with open(path, 'rb') as f:
                tmp_dict = pickle.load(f)
        except EOFError as e:
            print(f"Error loading statistics: {e}")
        self.stats.update(tmp_dict)

    def calculate_tables_statistics(self):
        '''
        Calculate statistics for all the tables in the database.
        '''
        if self.tables == {}: # if no tables in db.
            return # do nothing
        
        stats = {}
        for table_name in self.tables:
            if table_name.startswith('meta'):
                continue
            table = self.tables[table_name] # get table object
            size = len(table.data) # number of rows
            column_names = table.column_names # list of column names
            columns = {}
            for col_name in column_names:
                distinct_values = [row for row in table.column_by_name(col_name)]
                distinct_values = len(set(distinct_values))
                columns[col_name] = {"distinct_values": distinct_values}
            stats[table_name] = {
                    "size": size,
                    "columns": columns
                }
        self.stats = stats
        self.save_statistics()

    def print_statistics(self):
        '''
        Print statistics for all the tables in the database.
        '''
        print()
        if self.stats == {}:
            print('No statistics available.')
            return
        pprint(self.stats)
        print()

    # indexes
    def create_index(self, index_name, on_clause, index_type='btree'):
        '''
        Creates an index on a specified table with the given name.

        Important:
            <> An index can only be created if the table exists and has either a primary key or a unique column (the column name must be specified).
            <> The index name and the indexed column cannot appear twice in meta_indexes.

        Args:
            <> index_name: string. Name of the created index.
            <> on_clause: dict. The 'on' clause of the index. Must contain the table name and the column name.
            <> index_type: string. The type of the index. Supported types: btree, hash. Default: btree.
        '''
        table_name = on_clause['table_name']
        column_name = on_clause['column_name']
        
        if table_name not in self.tables:
            raise Exception(f'Table "{table_name}" does not exist.')
        
        if index_type not in ['btree', 'hash']:
            raise Exception(f'Index type "{index_type}" is not supported. Supported types: btree, hash.')
        
        # check if table has a primary key or a unique column.
        if self.tables[table_name].pk_idx is None and self.tables[table_name].unique_columns is None:
            raise Exception('Cannot create index. Table must have a primary key or a unique column.')
        
        # check if the specified column is a primary key or a unique column.
        if column_name!=self.tables[table_name].pk and column_name not in self.tables[table_name].unique_columns:
            raise Exception('Cannot create index. The specified column is not a primary key or a unique column.')
        
        # check if index name already exists.
        if index_name in self.tables['meta_indexes'].column_by_name('index_name'):
            raise Exception('Cannot create index. Another index with the same index name already exists.')

        # check if the column is already indexed for the specified table.
        if [table_name, column_name] in [[row[0], row[1]] for row in self.tables['meta_indexes'].data]:
            raise Exception('Cannot create index. The given column is already indexed for the specified table.')
        
        # add the index to meta_indexes
        logging.info(f'Creating {index_type} index.')
        self.tables['meta_indexes']._insert([table_name, column_name, index_name, index_type])
        
        # create the actual index
        self._construct_index(table_name, column_name, index_name, index_type)
        self.save_database()

    def _construct_index(self, table_name, column_name, index_name, index_type):
        '''
        Construct the index and save it to the database.

        Args:
            <> table_name: string. Table name.
            <> column_name: string. Column name (can be the primary key or a unique column).
            <> index_name: string. Name of the created index.
            <> index_type: string. The type of the index. Supported types: btree, hash.
        '''
        # create the index
        if index_type=='btree':
            index = Btree(3) # 3 is arbitrary
        else: # index_type=='hash'
            index = ExtendibleHashing(1, 4)
          
        # for each record of column_name, insert the key and the index of the record in the btree or hash (depending on the index type).
        for idx, key in enumerate(self.tables[table_name].column_by_name(column_name)):
            if key is None:
                continue
            if index_type=='btree':
                index.insert(key, idx)
            else: # index_type=='hash'
                index._add(key, idx)
        
        # save the index to the database.
        self._save_index(index_name, index)

    def _has_index(self, table_name, column_name=None):
        '''
        Check whether the specified table has an index in the specified column.
        If column_name is None, check if the table has any index.

        Args:
            <> table_name: string. Table name (must be part of database).
            <> column_name: string. Column name (must be part of table). If None, check if the table has any index.
        '''
        if column_name is None: # check if the table has any index.
            return table_name in self.tables['meta_indexes'].column_by_name('table_name')
        # else check if the specified column is indexed.
        return [table_name, column_name] in [[row[0], row[1]] for row in self.tables['meta_indexes'].data]

    def _save_index(self, index_name, index):
        '''
        Save the index object.

        Args:
            <> index_name: string. Name of the created index.
            <> index: obj. The actual index object (btree object).
        '''
        try:
            os.mkdir(f'{self.savedir}/indexes')
        except:
            pass

        with open(f'{self.savedir}/indexes/meta_{index_name}_index.pkl', 'wb') as f:
            pickle.dump(index, f)

    def _load_idx(self, index_name):
        '''
        Load and return the specified index.

        Args:
            <> index_name: string. Name of created index.
        '''
        f = open(f'{self.savedir}/indexes/meta_{index_name}_index.pkl', 'rb')
        index = pickle.load(f)
        f.close()
        return index

    def drop_index(self, index_name):
        '''
        Drop index from current database.

        Args:
            <> index_name: string. Name of index.
        '''
        if index_name in self.tables['meta_indexes'].column_by_name('index_name'):
            self.delete_from('meta_indexes', f'index_name = {index_name}')

            if os.path.isfile(f'{self.savedir}/indexes/meta_{index_name}_index.pkl'):
                os.remove(f'{self.savedir}/indexes/meta_{index_name}_index.pkl')
            else:
                warnings.warn(f'"{self.savedir}/indexes/meta_{index_name}_index.pkl" not found.')

            self.save_database()