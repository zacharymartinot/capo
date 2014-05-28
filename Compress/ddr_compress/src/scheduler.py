import time, logging
# XXX deal with 'XXX' in code below
# XXX how to deal with the first/last file in each day (i.e. only 1 of 2 neighbors)

logger = logging.getLogger('scheduler')

FILE_PROCESSING_STAGES = ['UV-POT', 'UV', 'UVC', 'CLEAN-UV', 'UVCR', 'CLEAN-UVC',
    'ACQUIRE-NEIGHBORS', 'UVCRE', 'UVCRR', 'NPZ-POT', 'CLEAN-UVCRE', 'UVCRRE',
    'CLEAN-UVCRR', 'CLEAN-NPZ', 'CLEAN-NEIGHBORS', 'UVCRRE-POT', 'CLEAN-UVCR', 'COMPLETE']
FILE_PROCESSING_LINKS = {}
for i,k in enumerate(FILE_PROCESSING_STAGES[:-1]):
    FILE_PROCESSING_LINKS[k] = FILE_PROCESSING_STAGES[i+1]
FILE_PROCESSING_LINKS['COMPLETE'] = None
#FILE_PROCESSING_STAGES  = { # dict linking file status to the next action
#    'UV-POT': ('UV',),
#    'UV': ('UVC',),
#    'UVC': ('CLEAN-UV',),
#    'CLEAN-UV': ('UVCR',),
#    'UVCR': ('CLEAN-UVC',),
#    'CLEAN-UVC': ('ACQUIRE-NEIGHBORS',), # transfer UVCRs for neighbors not assigned to this still
#    'ACQUIRE-NEIGHBORS': ('UVCRE',),
#    'UVCRE': ('UVCRR',),
#    'UVCRR': ('NPZ-POT',),
#    'NPZ-POT': ('CLEAN-UVCRE',),
#    'CLEAN-UVCRE': ('UVCRRE',),
#    'UVCRRE': ('CLEAN-UVCRR',), # do we want uvcRRE to run on UVCR of neighbors, or UVCRR?  I think UVCR, because that's all we get for edge files.
#    'CLEAN-UVCRR': ('CLEAN-NPZ',),
#    'CLEAN-NPZ': ('CLEAN-NEIGHBORS',),
#    'CLEAN-NEIGHBORS': ('UVCRRE-POT',), # clean UVCRs for neighbors not assigned to this still
#    'UVCRRE-POT': ('CLEAN-UVCR',),
#    'CLEAN-UVCR': ('COMPLETE',),
#    'COMPLETE': None,
#}

FILE_PROCESSING_PREREQS = { # link task to prerequisite state of neighbors, key not present assumes no prereqs
    'ACQUIRE-NEIGHBORS': (FILE_PROCESSING_STAGES.index('UVCR'), FILE_PROCESSING_STAGES.index('CLEAN-UVCR')),
    'CLEAN-UVCR': (FILE_PROCESSING_STAGES.index('UVCRRE'),None),
}

class Action:
    '''An Action performs a task on a file, and is scheduled by a Scheduler.'''
    def __init__(self, f, task, neighbors, still, timeout=3600.):
        '''f:filename, task:target status, neighbor:adjacent files, 
        still:still action will run on.'''
        self.filename = f
        self.task = task
        self.neighbors = neighbors
        self.still = still
        self.priority = 0
        self.launch_time = -1
        self.timeout = timeout
    def set_priority(self, p):
        '''Assign a priority to this action.  Highest priorities are scheduled first.'''
        self.priority = p
    def has_prerequisites(self, dbi):
        '''For the given task, check that neighbors are in prerequisite state.
        We don't check that the center file is in the prerequisite state, 
        since this action could not have been generated otherwise.'''
        try: index1,index2 = FILE_PROCESSING_PREREQS[self.task]
        except(KeyError): # this task has no prereqs
            return True
        for n in self.neighbors:
            if n is None: continue # if no neighbor exists, don't wait on it
            status = dbi.get_file_status(n)
            index = FILE_PROCESSING_STAGES.index(status)
            if not index1 is None and index < index1: return False
            if not index2 is None and index >= index2: return False
        return True
    def launch(self, launch_time=None):
        '''Run this task.'''
        if launch_time is None: launch_time = time.time()
        self.launch_time = launch_time
        return self._command()
    def _command(self):
        '''Replace this function in a subclass to execute different tasks.'''
        return
    def timed_out(self, curtime=None):
        assert(self.launch_time > 0) # Error out if action was not launched
        if curtime is None: curtime = time.time()
        return curtime > self.launch_time + self.timeout
        
def action_cmp(x,y): return cmp(x.priority, y.priority)

class Scheduler:
    '''A Scheduler reads a DataBaseInterface to determine what Actions can be
    taken, and then schedules them on stills according to priority.'''
    def __init__(self, nstills=4, actions_per_still=8, blocksize=10):
        '''nstills: # of stills in system, 
        actions_per_still: # of actions that can be scheduled simultaneously
                           per still.'''
        self.nstills = nstills
        self.actions_per_still = actions_per_still
        self.blocksize = blocksize
        self.active_files = []
        self._active_file_dict = {}
        self.action_queue = []
        self.launched_actions = {}
        for still in xrange(nstills): self.launched_actions[still] = []
        self._run = False
    def quit(self):
        self._run = False
    def start(self, dbi, ActionClass=None):
        '''Begin scheduling (blocking).
        dbi: DataBaseInterface'''
        logger.info('Beginning scheduler loop')
        self._run = True
        while self._run: 
            self.get_new_active_files(dbi)
            self.update_action_queue(dbi, ActionClass)
            # Launch actions that can be scheduled
            for still in self.launched_actions:
                while len(self.launched_actions[still]) < self.actions_per_still:
                    try: a = self.pop_action_queue(still)
                    except(IndexError): # no actions can be taken on this still
                        logger.info('No actions available for still-%d\n' % still)
                        break # move on to next still
                    self.launch_action(a)
            self.clean_completed_actions(dbi)
    def pop_action_queue(self, still):
        '''Return highest priority action for the given still.'''
        for i in xrange(len(self.action_queue)):
            if self.action_queue[i].still == still:
                return self.action_queue.pop(i)
        raise IndexError('No actions available for still-%d\n' % still)
    def launch_action(self, a):
        '''Launch the specified Action and record its launch for tracking later.'''
        self.launched_actions[a.still].append(a)
        a.launch()
    def clean_completed_actions(self, dbi):
        '''Check launched actions for completion or timeout.'''
        for still in self.launched_actions:
            updated_actions = []
            for cnt, a in enumerate(self.launched_actions[still]):
                status = dbi.get_file_status(a.filename)
                if status == a.task:
                    logger.info('Task %s for file %s on still %d completed successfully.' % (a.task, a.filename, still))
                    # not adding to updated_actions removes this from list of launched actions
                elif a.timed_out(): 
                    logger.info('Task %s for file %s on still %d TIMED OUT.' % (a.task, a.filename, still))
                    # XXX make db entry for documentation
                    # XXX actually kill the process if alive
                else: # still active
                    updated_actions.append(a)
            self.launched_actions[still] = updated_actions
    def already_launched(self, action):
        '''Determine if this action has already been launched.  Enforces
        fact that only one valid action can be taken for a given file
        at any one time.'''
        for a in self.launched_actions[action.still]:
            if a.filename == action.filename: return True
        return False
    def get_new_active_files(self, dbi):
        '''Check for any new files that may have appeared.  Actions for
        these files may potentially take priority over ones currently
        active.'''
        # XXX If actions have been launched since the last time this
        #was called, clean_completed_actions() must be called first to ensure
        #that cleanup occurs before.  Is this true? if so, should add mechanism
        #to ensure ordering
        for f in dbi.ordered_files():
            if not dbi.is_completed(f) and not self._active_file_dict.has_key(f):
                    self._active_file_dict[f] = len(self.active_files)
                    self.active_files.append(f)
    def update_action_queue(self, dbi, ActionClass=None):
        '''Based on the current list of active files (which you might want
        to update first), generate a prioritized list of actions that 
        can be taken.'''
        actions = [self.get_action(dbi,f,ActionClass=ActionClass) for f in self.active_files]
        actions = [a for a in actions if not a is None] # remove unactionables
        actions = [a for a in actions if not self.already_launched(a)] # filter actions already launched
        for a in actions: a.set_priority(self.determine_priority(a,dbi))
        actions.sort(action_cmp, reverse=True) # place most important actions first
        self.action_queue = actions # completely throw out previous action list
    def get_action(self, dbi, f, ActionClass=None):
        '''Find the next actionable step for file f (one for which all
        prerequisites have been met.  Return None if no action is available.
        This function is allowed to return actions that have already been
        launched.
        ActionClass: a subclass of Action, for customizing actions.  
            None defaults to the standard Action'''
        status = dbi.get_file_status(f)
        next_step = FILE_PROCESSING_LINKS[status]
        if next_step is None: return None # file is complete
        neighbors = dbi.get_neighbors(f)
        still = self.file_to_still(f, dbi) 
        if ActionClass is None: ActionClass = Action
        a = ActionClass(f, next_step, neighbors, still)
        if a.has_prerequisites(dbi): return a
        else: return None
    def determine_priority(self, action, dbi):
        '''Assign a priority to an action based on its status and the time
        order of the file to which this action is attached.'''
        return dbi.file_index(action.filename) # prioritize any possible action on the newest file
        # XXX might want to prioritize finishing a file already started before
        # moving to the latest one (at least, up to a point) to avoid a
        # build up of partial files.  But if you prioritize files already
        # started too excessively, then the queue could eventually fill with
        # partially completed tasks that are failing for some reason
    def file_to_still(self, f, dbi):
        '''Return the still that a file should be transferred to.'''
        cnt = dbi.file_index(f)
        return (cnt / self.blocksize) % self.nstills

        
                    
                    
                    