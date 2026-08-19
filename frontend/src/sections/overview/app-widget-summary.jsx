import PropTypes from 'prop-types';

import Box from '@mui/material/Box';
import Card from '@mui/material/Card';
import Stack from '@mui/material/Stack';
import Typography from '@mui/material/Typography';


export default function AppWidgetSummary({ name, title, text, icon, color = 'primary', titleColor, sx, ...other }) {
  return (
    <Card
      component={Stack}
      spacing={3}
      direction="row"
      sx={{
        px: 3,
        py: 5,
        borderRadius: 2,
        ...sx,
      }}
      {...other}
    >
      {icon && <Box sx={{ width: 64, height: 64 }}>{icon}</Box>}

      <Stack spacing={0.5}>
      <Typography variant="subtitle2" sx={text.primary}>
          {name}
        </Typography>
        <Typography variant="h4" sx={{ color }}>
          {text}  {/* Display text as a string */}
        </Typography>
        <Typography variant="subtitle2" sx={{ color: titleColor || 'text.primary' }}>
          {title}
        </Typography>
      </Stack>
    </Card>
  );
}




AppWidgetSummary.propTypes = {
  name: PropTypes.string.isRequired,
  title: PropTypes.string.isRequired,
  text: PropTypes.string.isRequired, 
  icon: PropTypes.node,
  color: PropTypes.string,
  titleColor: PropTypes.string,
  sx: PropTypes.object
};

// Default props for optional props
AppWidgetSummary.defaultProps = {
  icon: null,
  color: 'primary',
  sx: {}
};


AppWidgetSummary.propTypes = {
  name: PropTypes.string.isRequired,
  color: PropTypes.string,
  icon: PropTypes.oneOfType([PropTypes.element, PropTypes.string]),
  sx: PropTypes.object,
  title: PropTypes.string,
  total: PropTypes.number,
};
