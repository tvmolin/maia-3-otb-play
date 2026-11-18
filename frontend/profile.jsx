import React from 'react';
import { useEffect, useState } from 'react';
import { Anchor, Button, MantineProvider, Paper, Table, Title } from '@mantine/core';
import { IconArrowLeft, IconCopy } from '@tabler/icons-react';
import { createRoot } from 'react-dom/client';
import '@mantine/core/styles.css';
import './style.css';

const data = window.__PROFILE_DATA__;

function Profile() {
  const [status, setStatus] = useState('');
  const [refreshError, setRefreshError] = useState('');
  const [matches, setMatches] = useState(data.matches);
  const [submitting, setSubmitting] = useState([]);
  const pendingIds = JSON.stringify(matches
    .filter((match) => ['pending', 'uploading'].includes(match.lichess?.status))
    .map((match) => match.match_id));

  useEffect(() => {
    const ids = new Set(JSON.parse(pendingIds));
    if (!ids.size) return undefined;
    const controller = new AbortController();
    const params = new URLSearchParams({ category: data.category });
    ids.forEach((id) => params.append('match_id', id));
    let timer;
    const updateStatuses = async () => {
      try {
        const response = await fetch(`/api/lichess/status?${params}`, {
          signal: controller.signal,
          cache: 'no-store',
        });
        if (!response.ok) throw new Error('Could not check Lichess import status.');
        const result = await response.json();
        if (controller.signal.aborted) return;
        const statuses = new Map(result.matches.map((match) => [match.match_id, match.lichess]));
        setMatches((current) => current.map((match) => {
          if (!ids.has(match.match_id)) return match;
          return {
            ...match,
            lichess: statuses.get(match.match_id) || {
              status: 'failed',
              error: 'Import status is unavailable; check Lichess before retrying.',
            },
          };
        }));
        setRefreshError('');
      } catch (error) {
        if (!controller.signal.aborted) {
          setRefreshError('Could not check Lichess import status. Retrying automatically.');
        }
      }
      if (!controller.signal.aborted) timer = window.setTimeout(updateStatuses, 2000);
    };
    void updateStatuses();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [pendingIds]);

  const copyPgn = async (pgn) => {
    try {
      await navigator.clipboard.writeText(pgn);
      setStatus('PGN copied to clipboard.');
    } catch {
      setStatus(`Clipboard access is unavailable. PGN: ${pgn}`);
    }
  };
  const importToLichess = async (matchId) => {
    setSubmitting((current) => [...current, matchId]);
    try {
      const response = await fetch(`/api/lichess/import/${encodeURIComponent(matchId)}`, { method: 'POST' });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not import the game.');
      setMatches((current) => current.map((match) => (
        match.match_id === matchId ? { ...match, lichess: { status: result.status } } : match
      )));
    } catch (error) {
      setStatus(error.message);
    } finally {
      setSubmitting((current) => current.filter((id) => id !== matchId));
    }
  };
  const categoryName = data.category[0].toUpperCase() + data.category.slice(1);
  const matchRows = matches.length ? matches.map((match) => (
    <tr key={`${match.finished_at}-${match.pgn_file}`}>
      <td><time dateTime={match.finished_at}>{new Date(match.finished_at).toLocaleString()}</time></td>
      <td>{match.opponent_name || 'Maia'} ({match.maia_elo})</td>
      <td>{(match.player_color || 'Unknown')[0].toUpperCase() + (match.player_color || 'Unknown').slice(1)}</td>
      <td>{match.outcome[0].toUpperCase() + match.outcome.slice(1)}</td>
      <td>{match.time_control}</td>
      <td className={match.rating_change > 0 ? 'rating-positive' : match.rating_change < 0 ? 'rating-negative' : ''}>
        {match.rating_change > 0 ? '+' : ''}{match.rating_change}
      </td>
      <td>{match.player_elo_after}</td>
      <td>
        {match.pgn ? (
          <Button size="compact-sm" variant="light" className="copy-match-pgn" onClick={() => copyPgn(match.pgn)} leftSection={<IconCopy size={14} />}>
            Copy PGN
          </Button>
        ) : 'No PGN saved'}
      </td>
      <td aria-live="polite">
        {match.lichess?.status === 'imported' && (
          <Anchor href={match.lichess.url} target="_blank" rel="noopener noreferrer">Lichess analysis</Anchor>
        )}
        {['pending', 'uploading'].includes(match.lichess?.status) && 'Importing…'}
        {match.lichess?.status === 'failed' && (
          <>
            <span>Import failed: {match.lichess.error}</span>
            {match.lichess_retry_available && (
              <Button size="compact-sm" variant="light" loading={submitting.includes(match.match_id)} onClick={() => importToLichess(match.match_id)}>Retry import</Button>
            )}
          </>
        )}
        {!match.lichess && (
          <Button
            size="compact-sm"
            variant="light"
            disabled={!match.lichess_import_available}
            loading={submitting.includes(match.match_id)}
            title={!match.lichess_import_available ? 'Configure Lichess and ensure a saved PGN is available.' : undefined}
            onClick={() => importToLichess(match.match_id)}
          >
            Import to Lichess
          </Button>
        )}
      </td>
    </tr>
  )) : (
    <tr><td className="empty-history" colSpan="9">No matches yet. Play a match to start your history.</td></tr>
  );

  return (
    <MantineProvider defaultColorScheme="dark" theme={{ primaryColor: 'lime', fontFamily: 'system-ui, -apple-system, "Segoe UI", sans-serif' }}>
      <main>
        <header className="page-header">
          <Title order={1}>Player profile</Title>
          <Anchor className="header-link" href="/"><IconArrowLeft size={16} /> Back to game</Anchor>
        </header>
        <div className="profile-layout">
          <nav className="profile-tabs" aria-label="Rating category">
            <Anchor href="/profile?category=blitz" aria-current={data.category === 'blitz' ? 'page' : undefined}>Blitz</Anchor>
            <Anchor href="/profile?category=rapid" aria-current={data.category === 'rapid' ? 'page' : undefined}>Rapid</Anchor>
          </nav>
          <Paper className="panel" component="section" aria-labelledby="rating-title">
            <Title order={2} id="rating-title">{categoryName} rating progress</Title>
            <div className="profile-summary"><span>{categoryName} rating</span><strong>{data.playerElo}</strong></div>
            <svg className="rating-chart" viewBox="0 0 800 250" role="img" aria-label={`${categoryName} rating progress from ${Math.round(data.chartMin)} to ${Math.round(data.chartMax)}`}>
              {data.ratingTicks.map((tick) => (
                <g key={tick.y}>
                  <line className="rating-chart-grid" x1="58" x2="780" y1={tick.y} y2={tick.y} />
                  <text className="rating-chart-axis-label" x="50" y={tick.y + 5} textAnchor="end">{tick.rating}</text>
                </g>
              ))}
              {data.ratingPoints.length > 1 && <polyline className="rating-chart-line" points={data.ratingPoints.map((point) => `${point.x},${point.y}`).join(' ')} />}
              {data.ratingPoints.map((point, index) => (
                <circle className="rating-chart-point" cx={point.x} cy={point.y} r="6" key={`${point.x}-${point.y}`}>
                  <title>Rating {point.rating}{index ? `, match ${index}` : ', starting rating'}</title>
                </circle>
              ))}
            </svg>
          </Paper>
          <Paper className="panel" component="section" aria-labelledby="history-title">
            <Title order={2} id="history-title">{categoryName} match history</Title>
            <h3 id="color-stats-title">Results by piece color</h3>
            <div className="profile-table-wrap">
              <Table className="profile-table color-stats" aria-labelledby="color-stats-title">
                <Table.Thead><Table.Tr><Table.Th>Color</Table.Th><Table.Th>Games</Table.Th><Table.Th>W-D-L</Table.Th><Table.Th>Win rate</Table.Th><Table.Th>Score rate</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>
                  {['white', 'black'].map((color) => {
                    const stats = data.colorStats[color];
                    return <Table.Tr key={color}><Table.Th>{color[0].toUpperCase() + color.slice(1)}</Table.Th><Table.Td>{stats.games}</Table.Td><Table.Td>{stats.wins}-{stats.draws}-{stats.losses}</Table.Td><Table.Td>{stats.win_percentage === null ? '—' : `${stats.win_percentage.toFixed(1)}%`}</Table.Td><Table.Td>{stats.score_percentage === null ? '—' : `${stats.score_percentage.toFixed(1)}%`}</Table.Td></Table.Tr>;
                  })}
                </Table.Tbody>
              </Table>
            </div>
            <div className="profile-table-wrap">
              <Table className="profile-table">
                <Table.Thead><Table.Tr><Table.Th>Date</Table.Th><Table.Th>Opponent</Table.Th><Table.Th>Piece color</Table.Th><Table.Th>Result</Table.Th><Table.Th>Time control</Table.Th><Table.Th>Rating change</Table.Th><Table.Th>Rating</Table.Th><Table.Th>PGN</Table.Th><Table.Th>Lichess</Table.Th></Table.Tr></Table.Thead>
                <Table.Tbody>{matchRows}</Table.Tbody>
              </Table>
            </div>
            <p id="pgn-copy-status" role="status" aria-live="polite">{status} {refreshError}</p>
          </Paper>
        </div>
      </main>
    </MantineProvider>
  );
}

createRoot(document.getElementById('root')).render(<Profile />);
