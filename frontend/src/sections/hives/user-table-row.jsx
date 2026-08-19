import { useState } from 'react';
import PropTypes from 'prop-types';

import Stack from '@mui/material/Stack';
import Popover from '@mui/material/Popover';
import TableRow from '@mui/material/TableRow';
import Checkbox from '@mui/material/Checkbox';
import MenuItem from '@mui/material/MenuItem';
import TableCell from '@mui/material/TableCell';
import Typography from '@mui/material/Typography';
import IconButton from '@mui/material/IconButton';

import Label from 'src/components/label';
import Iconify from 'src/components/iconify';

// ----------------------------------------------------------------------

function getColor(status) {
  if (status === 'Отличное') return 'success';
  if (status === 'Потенциальные риски') return 'warning';
  if (status === 'Под угрозой') return 'error';
  return 'default';
}

function riskColor(risk) {
  if (risk == null) return 'default';
  if (risk >= 0.7) return 'error';
  if (risk >= 0.35) return 'warning';
  return 'success';
}

const fmt = (v, unit, digits = 1) => (v == null ? '—' : `${v.toFixed(digits)}${unit}`);

export default function UserTableRow({
  selected,
  name,
  temp,
  humidity,
  weight,
  swarmRisk,
  status,
  handleClick,
}) {
  const [open, setOpen] = useState(null);

  const handleOpenMenu = (event) => {
    setOpen(event.currentTarget);
  };

  const handleCloseMenu = () => {
    setOpen(null);
  };

  return (
    <>
      <TableRow hover tabIndex={-1} role="checkbox" selected={selected}>
        <TableCell padding="checkbox">
          <Checkbox disableRipple checked={selected} onChange={handleClick} />
        </TableCell>

        <TableCell component="th" scope="row" padding="none">
          <Stack direction="row" alignItems="center" spacing={2}>
            <Typography variant="subtitle2" noWrap>
              {name}
            </Typography>
          </Stack>
        </TableCell>

        <TableCell>{fmt(temp, '°C')}</TableCell>

        <TableCell>{fmt(humidity, '%', 0)}</TableCell>

        <TableCell>{fmt(weight, ' кг')}</TableCell>

        <TableCell>
          <Label color={riskColor(swarmRisk)}>
            {swarmRisk == null ? '—' : `${Math.round(swarmRisk * 100)}%`}
          </Label>
        </TableCell>

        <TableCell>
          <Label color={getColor(status)}>{status}</Label>
        </TableCell>

        <TableCell align="right">
          <IconButton onClick={handleOpenMenu}>
            <Iconify icon="eva:more-vertical-fill" />
          </IconButton>
        </TableCell>
      </TableRow>

      <Popover
        open={Boolean(open)}
        anchorEl={open}
        onClose={handleCloseMenu}
        anchorOrigin={{ vertical: 'top', horizontal: 'left' }}
        transformOrigin={{ vertical: 'top', horizontal: 'right' }}
        PaperProps={{
          sx: { width: 140 },
        }}
      >
        <MenuItem onClick={handleCloseMenu}>
          <Iconify icon="eva:edit-fill" sx={{ mr: 2 }} />
          Edit
        </MenuItem>

        <MenuItem onClick={handleCloseMenu} sx={{ color: 'error.main' }}>
          <Iconify icon="eva:trash-2-outline" sx={{ mr: 2 }} />
          Delete
        </MenuItem>
      </Popover>
    </>
  );
}

UserTableRow.propTypes = {
  handleClick: PropTypes.func,
  humidity: PropTypes.number,
  name: PropTypes.string,
  selected: PropTypes.bool,
  status: PropTypes.string,
  swarmRisk: PropTypes.number,
  temp: PropTypes.number,
  weight: PropTypes.number,
};
