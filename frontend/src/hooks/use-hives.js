import { useState, useEffect } from 'react';

import { getHives } from 'src/api';
import { subscribeLive } from 'src/api/live';

// Demo rows shown when the backend is unreachable (offline demo mode).
const DEMO_HIVES = Array.from({ length: 12 }, (_, i) => ({
  id: `KZ-ALA-${String(i + 1).padStart(4, '0')}`,
  name: `Улей ${i + 1}`,
  hive_type: i % 3 === 0 ? 'langstroth' : 'dadant',
  latest_reading: {
    temp_brood_c: 34.4 + (i % 5) * 0.3,
    humidity_pct: 55 + (i % 7) * 2,
    weight_kg: 38 + i * 1.7,
    battery_v: 3.6 + (i % 4) * 0.1,
  },
  latest_prediction: {
    swarm_risk: [0.06, 0.12, 0.31, 0.08, 0.84, 0.15][i % 6],
    health_score: [0.92, 0.88, 0.55, 0.9, 0.34, 0.79][i % 6],
  },
  open_alerts: i % 6 === 4 ? 2 : 0,
}));

export default function useHives() {
  const [hives, setHives] = useState([]);
  const [loading, setLoading] = useState(true);
  const [demo, setDemo] = useState(false);

  useEffect(() => {
    let unsubscribe;
    let cancelled = false;

    getHives()
      .then((data) => {
        if (cancelled) return;
        setHives(data);
        setLoading(false);
        unsubscribe = subscribeLive((event) => {
          setHives((prev) =>
            prev.map((h) => {
              if (h.id !== event.data.hive_id) return h;
              if (event.type === 'telemetry')
                return { ...h, latest_reading: { ...h.latest_reading, ...event.data } };
              if (event.type === 'prediction') return { ...h, latest_prediction: event.data };
              if (event.type === 'alert') return { ...h, open_alerts: (h.open_alerts ?? 0) + 1 };
              return h;
            })
          );
        });
      })
      .catch(() => {
        if (cancelled) return;
        setHives(DEMO_HIVES);
        setDemo(true);
        setLoading(false);
      });

    return () => {
      cancelled = true;
      unsubscribe?.();
    };
  }, []);

  return { hives, loading, demo };
}
