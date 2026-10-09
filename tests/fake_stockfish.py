"""A stand-in for Stockfish that speaks just enough UCI for the helper tests: always plays e2e4 / e7e5."""
import sys

side = 'w'
for line in sys.stdin:
    line = line.strip()
    if line == 'uci':
        print('id name FakeFish\nuciok', flush=True)
    elif line == 'isready':
        print('readyok', flush=True)
    elif line.startswith('position fen '):
        side = line.split()[3]
    elif line.startswith('go'):
        print('info depth 7 score cp 20', flush=True)
        print('bestmove %s' % ('e2e4' if side == 'w' else 'e7e5'), flush=True)
    elif line == 'quit':
        break
