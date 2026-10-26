"""
1. What is an exception?

    In Python, an exception is an object: an instance of a class derived from
    BaseException. All built-in exceptions — such as ValueError, TypeError, and
    KeyError — inherit from Exception, which in turn inherits from BaseException.

    BaseException
      +-- SystemExit              # raised by sys.exit()
      +-- KeyboardInterrupt       # raised on Ctrl+C
      +-- GeneratorExit           # raised when a generator/coroutine is closed
      +-- Exception               # base class for all non-exit exceptions
            +-- ValueError
            +-- TypeError
            +-- KeyError
            +-- ... user-defined exceptions are normally derived from here

    Writing `raise ValueError("xx")` instantiates a ValueError object and
    injects it into Python's exception propagation mechanism.


2. What happens when `raise` is executed?

    The moment a `raise` statement is executed, the normal control flow of the
    current function is interrupted, and the interpreter begins searching for a
    matching handler by unwinding the call stack:

        1. It first checks whether the current frame is enclosed by a try
           statement with a matching except clause.
        2. If no matching handler is found in the current frame, the frame is
           popped from the call stack and the caller's frame is examined.
        3. This search continues upward through the call stack, frame by frame.
        4. If a matching except clause is encountered, control transfers to that
           clause; if none is found by the time the stack is exhausted, the
           interpreter terminates the program and prints a traceback.

    This process is known as "stack unwinding". Because the exception object
    carries its own type information, the interpreter determines whether an
    except clause matches by testing `isinstance(raised_exception, except_type)`.


3. What information does an exception object carry?

    Every exception instance is primarily associated with three pieces of
    information:

        * Its type (type(e))
            Determines which except clause will match it.

        * Its arguments / message (e.args, str(e))
            The values passed to the exception's constructor; for example, the
            "msg" in `raise ValueError("msg")` becomes e.args[0].

        * Its traceback (e.__traceback__)
            A record of the call stack at the point where the exception was
            raised, indicating where it originated and which frames were
            traversed before it was caught (if at all).
"""
