

import Container from '@mui/material/Container';
import Grid from '@mui/material/Unstable_Grid2';
import Typography from '@mui/material/Typography';

import AppCurrentVisits from '../app-current-visits';
import AppWebsiteVisits from '../app-website-visits';
import AppWidgetSummary from '../app-widget-summary';
import AppCurrentSubject from '../app-current-subject';

// ----------------------------------------------------------------------

export default function AppView() {
  return (
    <Container maxWidth="xl">
      <Typography variant="h4" sx={{ mb: 5 }}>
          Улей-1
      </Typography>

      <Grid container spacing={3}>
        <Grid xs={12} sm={6} md={3}>
          <AppWidgetSummary

            name="Температура"
            title = "2% отклонения от нормы"
            titleColor = "green"
            text = "33 °C"
            color="success"
            icon={<img alt="icon" src="/assets/icons/glass/temp.png" />}
          />
        </Grid>

        <Grid xs={12} sm={6} md={3}>
          <AppWidgetSummary
            name="Влажность"
            title = "На 13% выше нормы"
            titleColor = "red"
            text = "91%"
            icon={<img alt="icon" src="/assets/icons/glass/hum.png" />}
          />
        </Grid>

        <Grid xs={12} sm={6} md={3}>
          <AppWidgetSummary
            name="Уровень шума"
            title = "На 7% выше нормы"
            titleColor = "orange"
            text = "270 Hz"
            icon={<img alt="icon" src="/assets/icons/glass/sound.webp" />}
          />
        </Grid>

        <Grid xs={12} sm={6} md={3}>
          <AppWidgetSummary
            name="Вес"
            title = "На 11% больше, чем вчера"
            titleColor = "green"
            text = "29.73 kg"
            icon={<img alt="icon" src="/assets/icons/glass/weight.png" />}
          />
        </Grid>

        <Grid xs={12} md={4} lg={6}>
          <AppWebsiteVisits
          title="Температура"
          subheader="Подробная информация про температуру в улье"
          chart={{
            labels: [
              '00:00',
              '02:00',
              '04:00',
              '06:00',
              '08:00',
              '10:00',
              '12:00',
              '14:00',
              '16:00',
              '18:00',
              '20:00',
              '22:00',
            ],
            series: [
              {
                name: 'За сутки',
                type: 'line',
                fill: 'solid',
                data: [28, 19, 10, 24, 18, 12, 30, 34, 31, 27, 11, 25],
              },
              {
                name: 'За неделю',
                type: 'area',
                fill: 'gradient',
                data: [],
              },
              {
                name: 'За месяц',
                type: 'line',
                fill: 'solid',
                data: [],
              },
              {
                name: 'За год',
                type: 'line',
                fill: 'solid',
                data: [],
              },
            ],
          }}
        />
        
        </Grid>

        <Grid xs={12} md={4} lg={6}>
        <AppWebsiteVisits
          title="Влажность"
          subheader="Подробная информация про влажность в улье"
          chart={{
            labels: [
              '00:00',
              '02:00',
              '04:00',
              '06:00',
              '08:00',
              '10:00',
              '12:00',
              '14:00',
              '16:00',
              '18:00',
              '20:00',
              '22:00',
            ],
            series: [
              {
                name: 'За сутки',
                type: 'line',
                fill: 'solid',
                data: [24, 24, 10, 23, 16, 29, 40, 13, 11, 25, 17, 22],
              },
              {
                name: 'За неделю',
                type: 'area',
                fill: 'gradient',
                data: [],
              },
              {
                name: 'За месяц',
                type: 'line',
                fill: 'solid',
                data: [],
              },
              {
                name: 'За год',
                type: 'line',
                fill: 'solid',
                data: [],
              },
            ],
          }}
        />
        
        
        </Grid>
        
        <Grid xs={12} md={4} lg={6}>
        <AppWebsiteVisits
          title="Вес"
          subheader="Подробная информация про вес улья"
          chart={{
            labels: [
              '00:00',
              '02:00',
              '04:00',
              '06:00',
              '08:00',
              '10:00',
              '12:00',
              '14:00',
              '16:00',
              '18:00',
              '20:00',
              '22:00',
            ],
            series: [
              {
                name: 'За сутки',
                type: 'line',
                fill: 'solid',
                data: [13, 17, 38, 26, 17, 36, 33, 30, 25, 34, 18, 30],
              },
              {
                name: 'За неделю',
                type: 'area',
                fill: 'gradient',
                data: [],
              },
              {
                name: 'За месяц',
                type: 'line',
                fill: 'solid',
                data: [],
              },
              {
                name: 'За год',
                type: 'line',
                fill: 'solid',
                data: [],
              },
            ],
          }}
        />
        
        
        </Grid>
        <Grid xs={12} md={4} lg={6}>
        <AppWebsiteVisits
          title="Уровень углекислых газов"
          subheader="Подробная информация про углекислый газ в улье"
          chart={{
            labels: [
              '00:00',
              '02:00',
              '04:00',
              '06:00',
              '08:00',
              '10:00',
              '12:00',
              '14:00',
              '16:00',
              '18:00',
              '20:00',
              '22:00',
            ],
            series: [
              {
                name: 'За сутки',
                type: 'line',
                fill: 'solid',
                data: [38, 31, 14, 36, 12, 34, 26, 13, 40, 38, 13, 15],
              },
              {
                name: 'За неделю',
                type: 'area',
                fill: 'gradient',
                data: [],
              },
              {
                name: 'За месяц',
                type: 'line',
                fill: 'solid',
                data: [],
              },
              {
                name: 'За год',
                type: 'line',
                fill: 'solid',
                data: [],
              },
            ],
          }}
        />
        
        
        </Grid>
        <Grid xs={12} md={4} lg={6}>
        <AppWebsiteVisits
          title="Уровень шума"
          subheader="Подробная информация про уровень шума в улье"
          chart={{
            labels: [
              '00:00',
              '02:00',
              '04:00',
              '06:00',
              '08:00',
              '10:00',
              '12:00',
              '14:00',
              '16:00',
              '18:00',
              '20:00',
              '22:00',
            ],
            series: [
              {
                name: 'За сутки',
                type: 'line',
                fill: 'solid',
                data: [23, 40, 40, 28, 33, 34, 27, 34, 35, 33, 18, 32],
              },
              {
                name: 'За неделю',
                type: 'area',
                fill: 'gradient',
                data: [],
              },
              {
                name: 'За месяц',
                type: 'line',
                fill: 'solid',
                data: [],
              },
              {
                name: 'За год',
                type: 'line',
                fill: 'solid',
                data: [],
              },
            ],
          }}
        />
        
        
        </Grid>
        <Grid xs={12} md={4} lg={6}>
        <AppWebsiteVisits
          title="Пестициды"
          subheader="Подробная информация про уровень пестицидов в улье"
          chart={{
            labels: [
              '00:00',
              '02:00',
              '04:00',
              '06:00',
              '08:00',
              '10:00',
              '12:00',
              '14:00',
              '16:00',
              '18:00',
              '20:00',
              '22:00',
            ],
            series: [
              {
                name: 'За сутки',
                type: 'line',
                fill: 'solid',
                data: [16, 11, 26, 16, 31, 13, 15, 23, 17, 21, 30, 38],
              },
              {
                name: 'За неделю',
                type: 'area',
                fill: 'gradient',
                data: [],
              },
              {
                name: 'За месяц',
                type: 'line',
                fill: 'solid',
                data: [],
              },
              {
                name: 'За год',
                type: 'line',
                fill: 'solid',
                data: [],
              },
            ],
          }}
        />
        
        
        </Grid>
        <Grid xs={12} md={6} lg={6}>
          <AppCurrentVisits
            title="Состояние улья"
            chart={{
              series: [
                { label: 'Стабильно', value: 85},
                { label: 'Предупреждения', value: 9},
                { label: 'Риски', value: 6},
              ],
            }}
          />
        </Grid>

        
        
        <Grid xs={12} md={6} lg={6}>
          <AppCurrentSubject
            title="Оценка состояния по секциям"
            chart={{
              categories: ['Температура', 'Влажность', 'Уровень газов', 'Шум', 'Уровень пестицидов'],
              series: [
                { name: 'Оценка', data: [98, 87, 94, 89, 130] },
              ],
            }}
          />
        </Grid>

        </Grid>
    </Container>
  );
}
