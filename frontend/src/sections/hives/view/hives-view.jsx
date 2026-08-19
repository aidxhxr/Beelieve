import { useState } from 'react';

import Card from '@mui/material/Card';
import Stack from '@mui/material/Stack';
import Table from '@mui/material/Table';
import Button from '@mui/material/Button';
import Container from '@mui/material/Container';
import TableBody from '@mui/material/TableBody';
import Typography from '@mui/material/Typography';
import TableContainer from '@mui/material/TableContainer';
import TablePagination from '@mui/material/TablePagination';

import { users } from 'src/_mock/user';

import Iconify from 'src/components/iconify';
import Scrollbar from 'src/components/scrollbar';

import TableNoData from '../table-no-data';
import UserTableRow from '../user-table-row';
import UserTableHead from '../user-table-head';
import TableEmptyRows from '../table-empty-rows';
import UserTableToolbar from '../user-table-toolbar';
import { emptyRows, applyFilter, getComparator } from '../utils';

// ----------------------------------------------------------------------

// Helper function to randomly assign status
const getRandomStatus = () => {
  const statuses = ['Отличное', 'Потенциальные риски', 'Под угрозой'];
  return statuses[Math.floor(Math.random() * statuses.length)];
};

const getRandomDevice = () => {
  const devices = ['Улей Beelieve', 'Хаб BeBee'];
  return devices[Math.floor(Math.random() * devices.length)];
};

const getRandomName = () => {
  const names = ['Улей-1', 'Улей-2', 'Улей-3', 'Улей-4', 'Улей-5', 'Улей-6', 'Улей-7', 'Улей-8', 'Улей-9', 'Улей-10', 'Улей-11', 'Улей-12', 'Улей-13', 'Улей-14', 'Улей-15', 'Улей-16', 'Улей-17', 'Улей-18', 'Улей-19', 'Улей-20', 'Улей-21', 'Улей-22', 'Улей-23', 'Улей-24', 'Улей-25', 'Улей-26', 'Улей-27', 'Улей-28', 'Улей-29', 'Улей-30',];
  const buffer = new Uint32Array(1);
  window.crypto.getRandomValues(buffer);
  const randomIndex = buffer[0] % names.length;
  return names[randomIndex];
}

const getRandomSpecies = () => {
  const species = ["Карника",
  "Карпатка",
  "Кавказская",
  "Итальянская",
  "Бакфаст",
  "Среднерусская",
  "Украинская степная",
  "Приокская",
  "Памирская",
  "Павловская",
  "Лигурийская",
  "Алтайская",
  "Далматинская",
  "Кубанская",
  "Медоносная",
  "Пчела Жигулевская",
  "Северо-западная",
  "Башкирская",
  "Киргизская",
  "Таджикская",
  "Узбекская",
  "Сирийская",
  "Турецкая",
  "Греческая",
  "Македонская",
  "Молдавская",
  "Грузинская",
  "Армянская",
  "Азербайджанская",
  "Туркменская"];
  return species[Math.floor(Math.random() * species.length)];
}

const usersWithStatus = users.map(user => ({
  ...user,
  name: getRandomName(),
  company: getRandomDevice(),
  role: getRandomSpecies(),
  status: getRandomStatus(),
}));

export default function UserPage() {
  const [page, setPage] = useState(0);

  const [order, setOrder] = useState('asc');

  const [selected, setSelected] = useState([]);

  const [orderBy, setOrderBy] = useState('name');

  const [filterName, setFilterName] = useState('');

  const [rowsPerPage, setRowsPerPage] = useState(5);

  const handleSort = (event, id) => {
    const isAsc = orderBy === id && order === 'asc';
    if (id !== '') {
      setOrder(isAsc ? 'desc' : 'asc');
      setOrderBy(id);
    }
  };

  const handleSelectAllClick = (event) => {
    if (event.target.checked) {
      const newSelecteds = usersWithStatus.map((n) => n.name);
      setSelected(newSelecteds);
      return;
    }
    setSelected([]);
  };

  const handleClick = (event, name) => {
    const selectedIndex = selected.indexOf(name);
    let newSelected = [];
    if (selectedIndex === -1) {
      newSelected = newSelected.concat(selected, name);
    } else if (selectedIndex === 0) {
      newSelected = newSelected.concat(selected.slice(1));
    } else if (selectedIndex === selected.length - 1) {
      newSelected = newSelected.concat(selected.slice(0, -1));
    } else if (selectedIndex > 0) {
      newSelected = newSelected.concat(
        selected.slice(0, selectedIndex),
        selected.slice(selectedIndex + 1)
      );
    }
    setSelected(newSelected);
  };

  const handleChangePage = (event, newPage) => {
    setPage(newPage);
  };

  const handleChangeRowsPerPage = (event) => {
    setPage(0);
    setRowsPerPage(parseInt(event.target.value, 10));
  };

  const handleFilterByName = (event) => {
    setPage(0);
    setFilterName(event.target.value);
  };

  const dataFiltered = applyFilter({
    inputData: usersWithStatus,
    comparator: getComparator(order, orderBy),
    filterName,
  });

  const notFound = !dataFiltered.length && !!filterName;

  return (
    <Container>
      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={5}>
        <Typography variant="h4">Мои улья</Typography>

        <Button variant="contained" color="inherit" startIcon={<Iconify icon="eva:plus-fill" />}>
          Добавить устройство
        </Button>
      </Stack>

      <Card>
        <UserTableToolbar
          numSelected={selected.length}
          filterName={filterName}
          onFilterName={handleFilterByName}
        />

        <Scrollbar>
          <TableContainer sx={{ overflow: 'unset' }}>
            <Table sx={{ minWidth: 800 }}>
              <UserTableHead
                order={order}
                orderBy={orderBy}
                rowCount={usersWithStatus.length}
                numSelected={selected.length}
                onRequestSort={handleSort}
                onSelectAllClick={handleSelectAllClick}
                headLabel={[
                  { id: 'number', label: 'Название устройства' },
                  { id: 'company', label: 'Тип устройства' },
                  { id: 'role', label: 'Порода пчел' },
                  { id: 'isVerified', label: 'Ручная проверка', align: 'center' },
                  { id: 'status', label: 'Состояние улья' },
                  { id: '' },
                ]}
              />
              <TableBody>
                {dataFiltered
                  .slice(page * rowsPerPage, page * rowsPerPage + rowsPerPage)
                  .map((row) => (
                    <UserTableRow
                      key={row.id}
                      name={row.name}
                      role={row.role}
                      status={row.status}
                      company={row.company}
                      isVerified={row.isVerified}
                      selected={selected.indexOf(row.name) !== -1}
                      handleClick={(event) => handleClick(event, row.name)}
                    />
                  ))}

                <TableEmptyRows
                  height={77}
                  emptyRows={emptyRows(page, rowsPerPage, usersWithStatus.length)}
                />

                {notFound && <TableNoData query={filterName} />}
              </TableBody>
            </Table>
          </TableContainer>
        </Scrollbar>

        <TablePagination
          page={page}
          component="div"
          count={usersWithStatus.length}
          rowsPerPage={rowsPerPage}
          onPageChange={handleChangePage}
          rowsPerPageOptions={[5, 10, 25]}
          onRowsPerPageChange={handleChangeRowsPerPage}
        />
      </Card>
    </Container>
  );
}
